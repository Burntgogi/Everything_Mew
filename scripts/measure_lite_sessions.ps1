[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$PythonPath = "",
    [string]$SdkDll = $env:EVERYTHING_SDK_DLL,
    [string]$OutputDirectory = "",
    [ValidateRange(1, 64)]
    [int]$Sessions = 6,
    [ValidateRange(1, 1000)]
    [int]$Cycles = 10,
    [ValidateRange(0, 86400)]
    [int]$IdleSeconds = 60,
    [ValidateNotNullOrEmpty()]
    [string]$Query = "ext:py",
    [string]$Scope = "",
    [ValidateRange(1, 100)]
    [int]$Limit = 10,
    [ValidateSet("name", "path", "size", "date_modified")]
    [string]$Sort = "path",
    [switch]$Metadata,
    [ValidateRange(1, 600)]
    [int]$ResponseTimeoutSeconds = 30,
    [ValidateRange(1, 600)]
    [int]$ExitTimeoutSeconds = 15
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$MiB = 1MB
$PerProcessFloorBytes = 10MB
$ExpectedToolNames = @(
    "everything_count",
    "everything_search",
    "everything_status",
    "everything_syntax_help"
)
$ActiveSessions = New-Object System.Collections.ArrayList
$OwnedProcessRecords = New-Object System.Collections.ArrayList

function Test-ObjectProperty {
    param(
        [Parameter(Mandatory = $true)]
        [object]$InputObject,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    return $null -ne $InputObject.PSObject.Properties[$Name]
}

function Get-RemainingMilliseconds {
    param(
        [Parameter(Mandatory = $true)]
        [DateTime]$Deadline
    )

    [double]$remaining = ($Deadline - [DateTime]::UtcNow).TotalMilliseconds
    if ($remaining -le 0) {
        return 0
    }
    return [int][Math]::Min([int]::MaxValue, [Math]::Ceiling($remaining))
}

function Get-CimOperationTimeoutSeconds {
    param(
        [Parameter(Mandatory = $true)]
        [DateTime]$DeadlineUtc,
        [ValidateRange(1, 10)]
        [int]$MaximumSeconds = 2,
        [string]$Context = "CIM operation"
    )

    $remainingMilliseconds = Get-RemainingMilliseconds -Deadline $DeadlineUtc
    if ($remainingMilliseconds -le 0) {
        throw "$Context absolute deadline expired before CIM invocation."
    }
    [int]$wholeRemainingSeconds = [Math]::Floor([double]$remainingMilliseconds / 1000.0)
    if ($wholeRemainingSeconds -lt 1) {
        throw "$Context absolute deadline has less than one second remaining for CIM invocation."
    }
    return [uint32][Math]::Min($MaximumSeconds, $wholeRemainingSeconds)
}

function Get-BoundedCimProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Filter,
        [Parameter(Mandatory = $true)]
        [DateTime]$DeadlineUtc,
        [Parameter(Mandatory = $true)]
        [string]$Context
    )

    $remainingBefore = Get-RemainingMilliseconds -Deadline $DeadlineUtc
    if ($remainingBefore -le 0) {
        throw "$Context absolute deadline expired before CIM query."
    }
    [uint32]$operationTimeoutSeconds = Get-CimOperationTimeoutSeconds -DeadlineUtc $DeadlineUtc -Context $Context
    try {
        $result = @(
            Get-CimInstance -ClassName Win32_Process -Filter $Filter `
                -OperationTimeoutSec $operationTimeoutSeconds -ErrorAction Stop
        )
    }
    catch {
        if ((Get-RemainingMilliseconds -Deadline $DeadlineUtc) -le 0) {
            throw "$Context exceeded its absolute deadline during the bounded CIM query."
        }
        throw "$Context bounded CIM query failed: $($_.Exception.Message)"
    }
    $remainingAfter = Get-RemainingMilliseconds -Deadline $DeadlineUtc
    if ($remainingAfter -le 0) {
        throw "$Context exceeded its absolute deadline after the bounded CIM query."
    }
    return @($result)
}

function Wait-TaskUntilDeadline {
    param(
        [Parameter(Mandatory = $true)]
        [System.Threading.Tasks.Task]$Task,
        [Parameter(Mandatory = $true)]
        [DateTime]$Deadline
    )

    if ($Task.IsCompleted) {
        return $true
    }
    $remainingMilliseconds = Get-RemainingMilliseconds -Deadline $Deadline
    if ($remainingMilliseconds -le 0) {
        return $false
    }
    return $Task.Wait($remainingMilliseconds)
}

function Wait-ProcessUntilDeadline {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)]
        [DateTime]$Deadline
    )

    $Process.Refresh()
    if ($Process.HasExited) {
        return $true
    }
    $remainingMilliseconds = Get-RemainingMilliseconds -Deadline $Deadline
    if ($remainingMilliseconds -le 0) {
        return $false
    }
    return $Process.WaitForExit($remainingMilliseconds)
}

function Open-RetainedProcessHandle {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process
    )

    $Process.EnableRaisingEvents = $true
    $null = $Process.Handle
}

function Set-ConsoleInputEncoding {
    param(
        [Parameter(Mandatory = $true)]
        [System.Text.Encoding]$Encoding,
        [System.Diagnostics.Process]$StartedProcess = $null
    )

    [Console]::InputEncoding = $Encoding
}

function New-ProbeOwnershipSession {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$OwnedRegistry
    )

    return [pscustomobject]@{
        Backend = "probe"
        Index = 0
        Process = $Process
        RuntimeProcess = $null
        OwnedRegistry = $OwnedRegistry
        OwnedProcesses = New-Object System.Collections.ArrayList
        ForcedCleanup = $false
        DiscoveryComplete = $false
    }
}

function Initialize-ProbeOwnershipSession {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session
    )

    foreach ($name in @("Backend", "Index", "Process", "OwnedProcesses", "ForcedCleanup", "DiscoveryComplete")) {
        if (-not (Test-ObjectProperty -InputObject $Session -Name $name)) {
            throw "Probe ownership session omitted required property '$name'."
        }
    }
    if ($null -eq $Session.Process -or $null -eq $Session.OwnedProcesses) {
        throw "Probe ownership session did not retain its root process and exact-record collection."
    }
    return $Session
}

function Get-ExactProcessIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process
    )

    try {
        $Process.EnableRaisingEvents = $true
        $null = $Process.Handle
        $Process.Refresh()
        $startTimeUtc = $Process.StartTime.ToUniversalTime()
        [long]$startTimeUtcTicks = $startTimeUtc.Ticks
        if ($startTimeUtcTicks -le 0) {
            throw "start time ticks were not positive"
        }
        return [pscustomobject]@{
            pid = [int]$Process.Id
            startTimeUtc = $startTimeUtc.ToString("o")
            startTimeUtcTicks = $startTimeUtcTicks
        }
    }
    catch {
        throw "Could not retain exact process handle/start identity: $($_.Exception.Message)"
    }
}

function Register-OwnedProcess {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session,
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)]
        [string]$Role,
        [object]$ParentRecord = $null,
        [int]$Depth = 0,
        [string]$ImageName = "",
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$OwnedRegistry
    )

    [int]$parentPid = 0
    [long]$parentStartTimeUtcTicks = 0
    if ($null -ne $ParentRecord) {
        if (
            $ParentRecord.owned -ne $true -or $ParentRecord.actionable -ne $true -or
            $null -eq $ParentRecord.process -or $null -eq $ParentRecord.startTimeUtcTicks -or
            [long]$ParentRecord.startTimeUtcTicks -le 0 -or
            -not $Session.OwnedProcesses.Contains($ParentRecord)
        ) {
            throw "Cannot bind child ownership to a non-exact retained parent record."
        }
        $parentState = Get-OwnedProcessState -Record $ParentRecord
        if ($parentState.status -ne "alive-owned") {
            throw "Cannot register a descendant after exact parent PID $($ParentRecord.pid) stopped being alive-owned."
        }
        $parentPid = [int]$ParentRecord.pid
        $parentStartTimeUtcTicks = [long]$ParentRecord.startTimeUtcTicks
    }
    elseif ($Depth -gt 0) {
        throw "Descendant ownership requires an exact retained parent record."
    }

    $identity = Get-ExactProcessIdentity -Process $Process
    $existing = @(
        $OwnedRegistry | Where-Object {
            $_.owned -eq $true -and
            $_.backend -eq $Session.Backend -and
            $_.session -eq $Session.Index -and
            $_.pid -eq $identity.pid -and
            $null -ne $_.startTimeUtcTicks -and
            [long]$_.startTimeUtcTicks -eq [long]$identity.startTimeUtcTicks
        }
    ) | Select-Object -First 1
    if ($null -ne $existing) {
        if ($null -ne $ParentRecord -and (Get-OwnedProcessState -Record $ParentRecord).status -ne "alive-owned") {
            throw "Cannot add a descendant role after exact parent PID $($ParentRecord.pid) stopped being alive-owned."
        }
        $existing.roles = @($existing.roles + @($Role) | Select-Object -Unique)
        if (-not $Session.OwnedProcesses.Contains($existing)) {
            $null = $Session.OwnedProcesses.Add($existing)
        }
        if (-not [object]::ReferenceEquals($existing.process, $Process)) {
            $Process.Dispose()
        }
        return $existing
    }

    if ([string]::IsNullOrWhiteSpace($ImageName)) {
        $ImageName = "$($Process.ProcessName).exe"
    }
    $record = [pscustomobject]@{
        backend = $Session.Backend
        session = $Session.Index
        pid = [int]$identity.pid
        parentPid = $parentPid
        parentStartTimeUtcTicks = $parentStartTimeUtcTicks
        depth = $Depth
        imageName = $ImageName
        roles = @($Role)
        owned = $true
        actionable = $true
        startTimeUtc = [string]$identity.startTimeUtc
        startTimeUtcTicks = [long]$identity.startTimeUtcTicks
        discoveredAtUtc = [DateTime]::UtcNow.ToString("o")
        observationStatus = $null
        issue = $null
        process = $Process
    }
    if ($null -ne $ParentRecord -and (Get-OwnedProcessState -Record $ParentRecord).status -ne "alive-owned") {
        throw "Cannot commit descendant ownership after exact parent PID $($ParentRecord.pid) stopped being alive-owned."
    }
    $null = $OwnedRegistry.Add($record)
    $null = $Session.OwnedProcesses.Add($record)
    return $record
}

function Register-ProcessObservation {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session,
        [Parameter(Mandatory = $true)]
        [int]$ProcessId,
        [Parameter(Mandatory = $true)]
        [string]$Status,
        [int]$ParentPid = 0,
        [long]$ParentStartTimeUtcTicks = 0,
        [int]$Depth = 0,
        [string]$ImageName = "",
        [string]$Issue = "",
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$OwnedRegistry
    )

    $existing = @(
        $OwnedRegistry | Where-Object {
            $_.owned -eq $false -and $_.backend -eq $Session.Backend -and $_.session -eq $Session.Index -and
            $_.pid -eq $ProcessId -and $_.observationStatus -eq $Status
        }
    ) | Select-Object -First 1
    if ($null -ne $existing) {
        return $existing
    }
    $record = [pscustomobject]@{
        backend = $Session.Backend
        session = $Session.Index
        pid = $ProcessId
        parentPid = $ParentPid
        parentStartTimeUtcTicks = $ParentStartTimeUtcTicks
        depth = $Depth
        imageName = $ImageName
        roles = @("observation")
        owned = $false
        actionable = $false
        startTimeUtc = $null
        startTimeUtcTicks = $null
        discoveredAtUtc = [DateTime]::UtcNow.ToString("o")
        observationStatus = $Status
        issue = $Issue
        process = $null
    }
    $null = $OwnedRegistry.Add($record)
    $null = $Session.OwnedProcesses.Add($record)
    return $record
}

function Sync-OwnedDescendants {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$OwnedRegistry,
        [Parameter(Mandatory = $true)]
        [DateTime]$DeadlineUtc,
        [switch]$BestEffort
    )

    $launcherRecord = @(
        $Session.OwnedProcesses | Where-Object { $_.owned -eq $true -and $_.roles -contains "launcher" }
    ) | Select-Object -First 1
    if ($null -eq $launcherRecord) {
        throw "$($Session.Backend) session $($Session.Index) has no exact launcher record."
    }
    $queue = New-Object System.Collections.Queue
    foreach ($ownedRecord in @(
            $Session.OwnedProcesses |
                Where-Object { $_.owned -eq $true } |
                Sort-Object depth
        )) {
        $queue.Enqueue($ownedRecord)
    }
    $visited = @{}
    $discovered = New-Object System.Collections.ArrayList
    while ($queue.Count -gt 0) {
        if ((Get-RemainingMilliseconds -Deadline $DeadlineUtc) -le 0) {
            throw "$($Session.Backend) session $($Session.Index) descendant discovery exceeded its absolute deadline."
        }
        $parent = $queue.Dequeue()
        $parentKey = "$($parent.pid):$($parent.startTimeUtcTicks)"
        if ($visited.ContainsKey($parentKey)) {
            continue
        }
        $visited[$parentKey] = $true
        $parentState = Get-OwnedProcessState -Record $parent
        if ($parentState.status -eq "exited") {
            continue
        }
        if ($parentState.status -ne "alive-owned") {
            throw "$($Session.Backend) session $($Session.Index) cannot enumerate descendants from non-exact parent PID $($parent.pid): $($parentState.status)"
        }
        try {
            $children = @(
                Get-BoundedCimProcess -Filter "ParentProcessId = $($parent.pid)" -DeadlineUtc $DeadlineUtc `
                    -Context "$($Session.Backend) session $($Session.Index) child enumeration for PID $($parent.pid)"
            )
        }
        catch {
            if ($BestEffort) {
                throw "$($Session.Backend) session $($Session.Index) could not enumerate exact descendants of PID $($parent.pid) during cleanup: $($_.Exception.Message)"
            }
            throw "$($Session.Backend) session $($Session.Index) could not enumerate exact descendants of PID $($parent.pid): $($_.Exception.Message)"
        }
        $parentState = Get-OwnedProcessState -Record $parent
        if ($parentState.status -ne "alive-owned") {
            throw "$($Session.Backend) session $($Session.Index) exact parent PID $($parent.pid) exited or changed identity during child enumeration."
        }
        foreach ($child in $children) {
            if ((Get-RemainingMilliseconds -Deadline $DeadlineUtc) -le 0) {
                throw "$($Session.Backend) session $($Session.Index) descendant discovery exceeded its absolute deadline."
            }
            [int]$childPid = $child.ProcessId
            [int]$childDepth = [int]$parent.depth + 1
            $parentState = Get-OwnedProcessState -Record $parent
            if ($parentState.status -ne "alive-owned") {
                throw "$($Session.Backend) session $($Session.Index) exact parent PID $($parent.pid) exited or changed identity before child ownership capture."
            }
            $childProcess = $null
            try {
                $childProcess = [System.Diagnostics.Process]::GetProcessById($childPid)
                $null = $childProcess.Handle
                $null = Get-ExactProcessIdentity -Process $childProcess
            }
            catch {
                $captureIssue = $_.Exception.Message
                $exitedBeforeIdentity = $null -eq $childProcess -and $_.Exception -is [System.ArgumentException]
                if ($null -ne $childProcess) {
                    try {
                        $childProcess.Refresh()
                        $exitedBeforeIdentity = $childProcess.HasExited
                    }
                    catch {
                        $exitedBeforeIdentity = $false
                        $captureIssue = "$captureIssue; retained-handle exit check failed: $($_.Exception.Message)"
                    }
                    $childProcess.Dispose()
                }
                if ($exitedBeforeIdentity) {
                    $null = Register-ProcessObservation -Session $Session -ProcessId $childPid `
                        -Status "exited-before-handle" -ParentPid ([int]$parent.pid) -Depth $childDepth `
                        -ParentStartTimeUtcTicks ([long]$parent.startTimeUtcTicks) `
                        -ImageName ([string]$child.Name) -Issue $captureIssue -OwnedRegistry $OwnedRegistry
                    continue
                }
                $null = Register-ProcessObservation -Session $Session -ProcessId $childPid `
                    -Status "unverifiable-live" -ParentPid ([int]$parent.pid) -Depth $childDepth `
                    -ParentStartTimeUtcTicks ([long]$parent.startTimeUtcTicks) `
                    -ImageName ([string]$child.Name) -Issue $captureIssue -OwnedRegistry $OwnedRegistry
                throw "$($Session.Backend) session $($Session.Index) found live descendant PID $childPid without an exact retained handle/start identity."
            }

            $currentChild = @()
            try {
                $currentChild = @(
                    Get-BoundedCimProcess -Filter "ProcessId = $childPid" -DeadlineUtc $DeadlineUtc `
                        -Context "$($Session.Backend) session $($Session.Index) parent verification for PID $childPid"
                )
            }
            catch {
                $childProcess.Refresh()
                if (-not $childProcess.HasExited) {
                    $childProcess.Dispose()
                    $null = Register-ProcessObservation -Session $Session -ProcessId $childPid `
                        -Status "unverifiable-live" -ParentPid ([int]$parent.pid) -Depth $childDepth `
                        -ParentStartTimeUtcTicks ([long]$parent.startTimeUtcTicks) `
                        -ImageName ([string]$child.Name) -Issue $_.Exception.Message -OwnedRegistry $OwnedRegistry
                    throw "$($Session.Backend) session $($Session.Index) could not verify parentage for live descendant PID $childPid."
                }
            }
            if ($currentChild.Count -ne 1) {
                $childProcess.Refresh()
                if ($childProcess.HasExited) {
                    $childProcess.Dispose()
                    $null = Register-ProcessObservation -Session $Session -ProcessId $childPid `
                        -Status "exited-before-parent-verification" -ParentPid ([int]$parent.pid) `
                        -ParentStartTimeUtcTicks ([long]$parent.startTimeUtcTicks) -Depth $childDepth `
                        -ImageName ([string]$child.Name) -OwnedRegistry $OwnedRegistry
                    continue
                }
                $childProcess.Dispose()
                $null = Register-ProcessObservation -Session $Session -ProcessId $childPid `
                    -Status "unverifiable-live" -ParentPid ([int]$parent.pid) `
                    -ParentStartTimeUtcTicks ([long]$parent.startTimeUtcTicks) -Depth $childDepth `
                    -ImageName ([string]$child.Name) -Issue "parent verification returned $($currentChild.Count) rows" `
                    -OwnedRegistry $OwnedRegistry
                throw "$($Session.Backend) session $($Session.Index) could not bind live descendant PID $childPid to one exact retained parent."
            }
            if ($currentChild.Count -eq 1 -and [int]$currentChild[0].ParentProcessId -ne [int]$parent.pid) {
                $childProcess.Dispose()
                $null = Register-ProcessObservation -Session $Session -ProcessId $childPid `
                    -Status "pid-reused-before-ownership" -ParentPid ([int]$parent.pid) -Depth $childDepth `
                    -ParentStartTimeUtcTicks ([long]$parent.startTimeUtcTicks) `
                    -ImageName ([string]$child.Name) -OwnedRegistry $OwnedRegistry
                continue
            }
            $parentState = Get-OwnedProcessState -Record $parent
            if ($parentState.status -ne "alive-owned") {
                $childProcess.Dispose()
                throw "$($Session.Backend) session $($Session.Index) exact parent PID $($parent.pid) exited or changed identity before descendant registration."
            }
            try {
                $record = Register-OwnedProcess -Session $Session -Process $childProcess -Role "descendant" `
                    -ParentRecord $parent -Depth $childDepth -ImageName ([string]$child.Name) `
                    -OwnedRegistry $OwnedRegistry
            }
            catch {
                $childProcess.Dispose()
                throw
            }
            $null = $discovered.Add($record)
            $queue.Enqueue($record)
        }
    }
    return @($discovered)
}

function Get-OwnedProcessState {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Record
    )

    if (
        $Record.owned -ne $true -or $Record.actionable -ne $true -or $null -eq $Record.process -or
        $null -eq $Record.startTimeUtcTicks -or [long]$Record.startTimeUtcTicks -le 0
    ) {
        return [pscustomobject]@{
            status = "unreadable"
            alive = $null
            currentStartTimeUtc = $null
            currentStartTimeUtcTicks = $null
            issue = "owned process record lacked an exact retained handle/start identity"
        }
    }
    try {
        $identity = Get-ExactProcessIdentity -Process $Record.process
        if ([long]$identity.startTimeUtcTicks -ne [long]$Record.startTimeUtcTicks) {
            return [pscustomobject]@{
                status = "pid-reused"
                alive = $false
                currentStartTimeUtc = $identity.startTimeUtc
                currentStartTimeUtcTicks = [long]$identity.startTimeUtcTicks
                issue = $null
            }
        }
        $Record.process.Refresh()
        if ($Record.process.HasExited) {
            return [pscustomobject]@{
                status = "exited"
                alive = $false
                currentStartTimeUtc = $identity.startTimeUtc
                currentStartTimeUtcTicks = [long]$identity.startTimeUtcTicks
                issue = $null
            }
        }
        return [pscustomobject]@{
            status = "alive-owned"
            alive = $true
            currentStartTimeUtc = $identity.startTimeUtc
            currentStartTimeUtcTicks = [long]$identity.startTimeUtcTicks
            issue = $null
        }
    }
    catch {
        return [pscustomobject]@{
            status = "unreadable"
            alive = $null
            currentStartTimeUtc = $null
            currentStartTimeUtcTicks = $null
            issue = $_.Exception.Message
        }
    }
}

function Stop-RetainedProcessHandle {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process,
        [ValidateRange(1, 30)]
        [int]$TimeoutSeconds = 5
    )

    $issues = New-Object System.Collections.ArrayList
    $killAttempted = $false
    $waitCompleted = $false
    $exited = $false
    try {
        $Process.Refresh()
        $exited = $Process.HasExited
    }
    catch {
        $null = $issues.Add("retained root initial state check failed: $($_.Exception.Message)")
    }
    if (-not $exited) {
        try {
            $killAttempted = $true
            $Process.Kill()
        }
        catch {
            $null = $issues.Add("retained root kill failed: $($_.Exception.Message)")
        }
        try {
            $waitCompleted = $Process.WaitForExit($TimeoutSeconds * 1000)
            if (-not $waitCompleted) {
                $null = $issues.Add("retained root did not exit within $TimeoutSeconds seconds")
            }
        }
        catch {
            $null = $issues.Add("retained root bounded wait failed: $($_.Exception.Message)")
        }
    }
    else {
        $waitCompleted = $true
    }
    try {
        $Process.Refresh()
        $exited = $Process.HasExited
    }
    catch {
        $exited = $false
        $null = $issues.Add("retained root final exit verification failed: $($_.Exception.Message)")
    }
    return [pscustomobject]@{
        pass = $issues.Count -eq 0 -and $waitCompleted -and $exited
        killAttempted = $killAttempted
        waitCompleted = $waitCompleted
        exited = $exited
        issue = if ($issues.Count) { @($issues) -join "; " } else { $null }
    }
}

function Stop-ExactOwnedRecord {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session,
        [Parameter(Mandatory = $true)]
        [object]$Record,
        [ValidateRange(1, 30)]
        [int]$TimeoutSeconds = 5
    )

    $state = Get-OwnedProcessState -Record $Record
    if ($state.status -eq "exited") {
        return [pscustomobject]@{
            pid = [int]$Record.pid
            startTimeUtcTicks = [long]$Record.startTimeUtcTicks
            killAttempted = $false
            waitCompleted = $true
            exited = $true
            statusBefore = "exited"
            issue = $null
        }
    }
    if ($state.status -ne "alive-owned") {
        return [pscustomobject]@{
            pid = [int]$Record.pid
            startTimeUtcTicks = $Record.startTimeUtcTicks
            killAttempted = $false
            waitCompleted = $false
            exited = $false
            statusBefore = $state.status
            issue = "owned PID $($Record.pid) was not safe for exact handle cleanup: $($state.status); $($state.issue)"
        }
    }

    $Session.ForcedCleanup = $true
    $waitCompleted = $false
    $issue = $null
    try {
        $Record.process.Kill()
        $waitCompleted = $Record.process.WaitForExit($TimeoutSeconds * 1000)
        if (-not $waitCompleted) {
            $issue = "exact retained PID $($Record.pid) did not exit within $TimeoutSeconds seconds"
        }
    }
    catch {
        $issue = "exact retained PID $($Record.pid) cleanup failed: $($_.Exception.Message)"
    }
    $postState = Get-OwnedProcessState -Record $Record
    $exited = $postState.status -eq "exited"
    if ($exited -and -not $waitCompleted) {
        $waitCompleted = $true
        $issue = $null
    }
    elseif (-not $exited -and [string]::IsNullOrWhiteSpace($issue)) {
        $issue = "exact retained PID $($Record.pid) remained $($postState.status) after cleanup"
    }
    return [pscustomobject]@{
        pid = [int]$Record.pid
        startTimeUtcTicks = [long]$Record.startTimeUtcTicks
        killAttempted = $true
        waitCompleted = $waitCompleted
        exited = $exited
        statusBefore = $state.status
        issue = $issue
    }
}

function Stop-ExactOwnedProcessTree {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$OwnedRegistry,
        [ValidateRange(1, 30)]
        [int]$DiscoveryTimeoutSeconds = 3,
        [ValidateRange(1, 30)]
        [int]$HandleWaitTimeoutSeconds = 5
    )

    $issues = New-Object System.Collections.ArrayList
    $warnings = New-Object System.Collections.ArrayList
    $fallbackAttemptedPids = New-Object System.Collections.ArrayList
    $verification = New-Object System.Collections.ArrayList
    $cleanupOutcomes = @{}
    $discoveryComplete = $false
    $discoveryIssue = $null
    $discoveryPasses = 0
    $stableReconciliationPasses = 0
    $stableReconciliationPassesRequired = 2
    $discoveryDeadline = [DateTime]::UtcNow.AddSeconds($DiscoveryTimeoutSeconds)
    $launcherRecords = @(
        $Session.OwnedProcesses | Where-Object { $_.owned -eq $true -and $_.roles -contains "launcher" }
    )
    $treeKill = [pscustomobject]@{
        pass = $true
        invoked = $false
        waitCompleted = $true
        exitCode = $null
        method = "retained-handles-child-first"
        issue = $null
    }
    if ($launcherRecords.Count -ne 1) {
        $null = $issues.Add("session did not retain exactly one launcher identity for cleanup")
    }
    else {
        while (
            (Get-RemainingMilliseconds -Deadline $discoveryDeadline) -gt 0 -and
            $stableReconciliationPasses -lt $stableReconciliationPassesRequired
        ) {
            $recordCountBefore = @($Session.OwnedProcesses).Count
            try {
                $null = @(
                    Sync-OwnedDescendants -Session $Session -OwnedRegistry $OwnedRegistry `
                        -DeadlineUtc $discoveryDeadline -BestEffort
                )
                $discoveryPasses += 1
                $discoveryComplete = $true
            }
            catch {
                $discoveryComplete = $false
                $discoveryIssue = $_.Exception.Message
                $null = $warnings.Add($discoveryIssue)
                break
            }

            foreach ($record in @(
                    $Session.OwnedProcesses |
                        Where-Object { $_.owned -eq $true } |
                        Sort-Object depth -Descending
                )) {
                $recordKey = "$($record.pid):$($record.startTimeUtcTicks)"
                if ($cleanupOutcomes.ContainsKey($recordKey)) {
                    continue
                }
                $outcome = Stop-ExactOwnedRecord -Session $Session -Record $record `
                    -TimeoutSeconds $HandleWaitTimeoutSeconds
                $cleanupOutcomes[$recordKey] = $outcome
                if ($outcome.killAttempted) {
                    $null = $fallbackAttemptedPids.Add([int]$outcome.pid)
                }
                if (-not [string]::IsNullOrWhiteSpace([string]$outcome.issue)) {
                    $null = $issues.Add($outcome.issue)
                }
            }

            $ownedStates = @(
                $Session.OwnedProcesses |
                    Where-Object { $_.owned -eq $true } |
                    ForEach-Object { Get-OwnedProcessState -Record $_ }
            )
            $allOwnedExited = $ownedStates.Count -gt 0 -and @(
                $ownedStates | Where-Object { $_.status -ne "exited" }
            ).Count -eq 0
            $recordCountAfter = @($Session.OwnedProcesses).Count
            if ($allOwnedExited -and $recordCountAfter -eq $recordCountBefore) {
                $stableReconciliationPasses += 1
            }
            else {
                $stableReconciliationPasses = 0
            }
            if ($stableReconciliationPasses -lt $stableReconciliationPassesRequired) {
                $remainingMilliseconds = Get-RemainingMilliseconds -Deadline $discoveryDeadline
                if ($remainingMilliseconds -gt 0) {
                    Start-Sleep -Milliseconds ([Math]::Min(25, $remainingMilliseconds))
                }
            }
        }
    }

    # Even when bounded reconciliation fails, terminate every exact handle captured so far.
    foreach ($record in @(
            $Session.OwnedProcesses |
                Where-Object { $_.owned -eq $true } |
                Sort-Object depth -Descending
        )) {
        $recordKey = "$($record.pid):$($record.startTimeUtcTicks)"
        if ($cleanupOutcomes.ContainsKey($recordKey)) {
            continue
        }
        $outcome = Stop-ExactOwnedRecord -Session $Session -Record $record `
            -TimeoutSeconds $HandleWaitTimeoutSeconds
        $cleanupOutcomes[$recordKey] = $outcome
        if ($outcome.killAttempted) {
            $null = $fallbackAttemptedPids.Add([int]$outcome.pid)
        }
        if (-not [string]::IsNullOrWhiteSpace([string]$outcome.issue)) {
            $null = $issues.Add($outcome.issue)
        }
    }

    $waitResults = @()
    foreach ($record in @(
            $Session.OwnedProcesses |
                Where-Object { $_.owned -eq $true } |
                Sort-Object depth -Descending
        )) {
        $recordKey = "$($record.pid):$($record.startTimeUtcTicks)"
        if ($cleanupOutcomes.ContainsKey($recordKey)) {
            $waitResults += $cleanupOutcomes[$recordKey]
        }
    }
    foreach ($observation in @($Session.OwnedProcesses | Where-Object { $_.owned -eq $false })) {
        if ($observation.observationStatus -eq "unverifiable-live") {
            $null = $issues.Add("unverifiable live observation PID $($observation.pid) was not terminated")
        }
    }
    foreach ($record in @($Session.OwnedProcesses | Where-Object { $_.owned -eq $true })) {
        $state = Get-OwnedProcessState -Record $record
        $null = $verification.Add([pscustomobject]@{
                pid = [int]$record.pid
                startTimeUtcTicks = $record.startTimeUtcTicks
                status = $state.status
                issue = $state.issue
            })
        if ($state.status -ne "exited") {
            $null = $issues.Add("exact retained PID $($record.pid) was not proven exited after cleanup: $($state.status)")
        }
    }
    $allCapturedExited = @($verification | Where-Object { $_.status -ne "exited" }).Count -eq 0 -and
        $verification.Count -gt 0
    $treeTerminationProven = $allCapturedExited -and $discoveryComplete -and
        $stableReconciliationPasses -ge $stableReconciliationPassesRequired
    $Session.DiscoveryComplete = $treeTerminationProven
    if (-not $treeTerminationProven) {
        $detail = if ($discoveryIssue) { $discoveryIssue } else { "stable post-exit reconciliation was incomplete" }
        $null = $issues.Add("exact process-tree exit could not be proven: $detail")
    }
    return [pscustomobject]@{
        pass = $issues.Count -eq 0 -and $treeTerminationProven
        discoveryComplete = $discoveryComplete
        discoveryDeadlineUtc = $discoveryDeadline.ToString("o")
        discoveryIssue = $discoveryIssue
        discoveryPasses = $discoveryPasses
        stableReconciliationPassesRequired = $stableReconciliationPassesRequired
        stableReconciliationPasses = $stableReconciliationPasses
        treeKill = $treeKill
        fallbackAttemptedPids = [int[]]@($fallbackAttemptedPids)
        waitResults = [object[]]@($waitResults)
        verification = [object[]]@($verification)
        treeTerminationProven = $treeTerminationProven
        warnings = [object[]]@($warnings)
        issues = [object[]]@($issues)
    }
}

function New-PidRecheckResult {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]]$Entries
    )

    [object[]]$entryValues = @($Entries)
    [int[]]$checkedPids = @($entryValues | ForEach-Object { [int]$_.pid })
    [int[]]$aliveOwnedPids = @(
        $entryValues | Where-Object { $_.status -eq "alive-owned" } | ForEach-Object { [int]$_.pid } | Sort-Object -Unique
    )
    [int[]]$reusedPids = @(
        $entryValues | Where-Object { $_.status -eq "pid-reused" } | ForEach-Object { [int]$_.pid } | Sort-Object -Unique
    )
    [int[]]$unreadablePids = @(
        $entryValues | Where-Object { $_.status -eq "unreadable" } | ForEach-Object { [int]$_.pid } | Sort-Object -Unique
    )
    return [pscustomobject]@{
        pass = $entryValues.Count -gt 0 -and $aliveOwnedPids.Count -eq 0 -and $reusedPids.Count -eq 0 -and `
            $unreadablePids.Count -eq 0 -and $checkedPids.Count -eq $entryValues.Count
        checkedPids = $checkedPids
        checkedCount = $checkedPids.Count
        aliveOwnedPids = $aliveOwnedPids
        aliveOwnedCount = $aliveOwnedPids.Count
        reusedPids = $reusedPids
        reusedCount = $reusedPids.Count
        unreadablePids = $unreadablePids
        unreadableCount = $unreadablePids.Count
        entries = $entryValues
    }
}

function Invoke-IndependentPidRecheck {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]]$AuditEntries
    )

    $entries = @()
    foreach ($owned in @($AuditEntries | Where-Object { $_.owned -eq $true })) {
        $status = "unreadable"
        $currentTicks = $null
        $issue = $null
        if ($null -eq $owned.startTimeUtcTicks -or [long]$owned.startTimeUtcTicks -le 0) {
            $issue = "owned evidence lacked exact start ticks"
        }
        else {
            $currentProcess = $null
            try {
                $currentProcess = [System.Diagnostics.Process]::GetProcessById([int]$owned.pid)
                $identity = Get-ExactProcessIdentity -Process $currentProcess
                $currentTicks = [long]$identity.startTimeUtcTicks
                if ($currentTicks -ne [long]$owned.startTimeUtcTicks) {
                    $status = "pid-reused"
                }
                else {
                    $currentProcess.Refresh()
                    $status = if ($currentProcess.HasExited) { "not-present" } else { "alive-owned" }
                }
            }
            catch [System.ArgumentException] {
                $status = "not-present"
            }
            catch {
                $status = "unreadable"
                $issue = $_.Exception.Message
            }
            finally {
                if ($null -ne $currentProcess) {
                    $currentProcess.Dispose()
                }
            }
        }
        $entries += [pscustomobject]@{
            backend = $owned.backend
            session = $owned.session
            pid = [int]$owned.pid
            recordedStartTimeUtcTicks = $owned.startTimeUtcTicks
            currentStartTimeUtcTicks = $currentTicks
            status = $status
            issue = $issue
        }
    }
    return New-PidRecheckResult -Entries @($entries)
}

function Invoke-OwnedPidAudit {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$OwnedRegistry
    )

    $entries = @()
    foreach ($record in @($OwnedRegistry)) {
        if ($record.owned -eq $true) {
            $state = Get-OwnedProcessState -Record $record
            $status = $state.status
            $currentStartTimeUtc = $state.currentStartTimeUtc
            $currentStartTimeUtcTicks = $state.currentStartTimeUtcTicks
            $issue = $state.issue
        }
        else {
            $status = [string]$record.observationStatus
            $currentStartTimeUtc = $null
            $currentStartTimeUtcTicks = $null
            $issue = $record.issue
        }
        $entries += [pscustomobject]@{
            backend = $record.backend
            session = $record.session
            pid = [int]$record.pid
            parentPid = [int]$record.parentPid
            parentStartTimeUtcTicks = [long]$record.parentStartTimeUtcTicks
            imageName = [string]$record.imageName
            roles = @($record.roles)
            owned = [bool]$record.owned
            actionable = [bool]$record.actionable
            startTimeUtc = $record.startTimeUtc
            startTimeUtcTicks = $record.startTimeUtcTicks
            currentStartTimeUtc = $currentStartTimeUtc
            currentStartTimeUtcTicks = $currentStartTimeUtcTicks
            status = $status
            issue = $issue
        }
    }
    [int[]]$checkedPids = @($entries | ForEach-Object { [int]$_.pid })
    [int[]]$alivePids = @(
        $entries | Where-Object { $_.status -eq "alive-owned" } | ForEach-Object { [int]$_.pid } | Sort-Object -Unique
    )
    [int[]]$reusedPids = @(
        $entries | Where-Object { $_.status -eq "pid-reused" -or $_.status -eq "pid-reused-before-ownership" } |
            ForEach-Object { [int]$_.pid } | Sort-Object -Unique
    )
    [int[]]$unreadablePids = @(
        $entries | Where-Object { $_.status -eq "unreadable" -or $_.status -eq "unverifiable-live" } |
            ForEach-Object { [int]$_.pid } | Sort-Object -Unique
    )
    $ownedEntries = @($entries | Where-Object { $_.owned -eq $true })
    $ownedIdentitiesComplete = @(
        $ownedEntries | Where-Object {
            $null -eq $_.startTimeUtcTicks -or [long]$_.startTimeUtcTicks -le 0 -or [string]::IsNullOrWhiteSpace([string]$_.startTimeUtc)
        }
    ).Count -eq 0
    $ownedEdgesComplete = $ownedIdentitiesComplete
    $ownedByIdentity = @{}
    foreach ($entry in $ownedEntries) {
        $ownedByIdentity["$([int]$entry.pid):$([long]$entry.startTimeUtcTicks)"] = $entry
    }
    foreach ($entry in $ownedEntries) {
        if ([int]$entry.parentPid -eq 0) {
            if ([long]$entry.parentStartTimeUtcTicks -ne 0) {
                $ownedEdgesComplete = $false
            }
            continue
        }
        if (
            [long]$entry.parentStartTimeUtcTicks -le 0 -or
            -not $ownedByIdentity.ContainsKey("$([int]$entry.parentPid):$([long]$entry.parentStartTimeUtcTicks)")
        ) {
            $ownedEdgesComplete = $false
        }
    }
    return [pscustomobject]@{
        pass = $entries.Count -gt 0 -and $checkedPids.Count -eq $entries.Count -and $ownedEntries.Count -gt 0 -and `
            $ownedIdentitiesComplete -and $ownedEdgesComplete -and $alivePids.Count -eq 0 -and $reusedPids.Count -eq 0 -and `
            $unreadablePids.Count -eq 0
        checkedPids = $checkedPids
        alivePids = $alivePids
        reusedPids = $reusedPids
        unreadablePids = $unreadablePids
        checkedCount = $checkedPids.Count
        aliveCount = $alivePids.Count
        reusedCount = $reusedPids.Count
        unreadableCount = $unreadablePids.Count
        ownedCount = $ownedEntries.Count
        observationCount = $entries.Count - $ownedEntries.Count
        ownedIdentitiesComplete = $ownedIdentitiesComplete
        ownedEdgesComplete = $ownedEdgesComplete
        entries = [object[]]@($entries)
    }
}

function Resolve-ExistingPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Description,
        [switch]$Leaf
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "$Description was not provided."
    }
    $pathType = if ($Leaf) { "Leaf" } else { "Container" }
    if (-not (Test-Path -LiteralPath $Path -PathType $pathType)) {
        throw "$Description does not exist as a $($pathType.ToLowerInvariant()): $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Complete-ProbeDiscovery {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$OwnedRegistry,
        [Parameter(Mandatory = $true)]
        [DateTime]$DeadlineUtc,
        [ValidateRange(2, 10)]
        [int]$StablePassesRequired = 2,
        [ValidateRange(10, 1000)]
        [int]$PollMilliseconds = 25
    )

    $rootExitObserved = $false
    $discoveryScanCount = 0
    $ownershipDiscoveryPasses = 0
    $postExitDiscoveryPasses = 0
    $postExitReconciliationPasses = 0
    $stablePassesObserved = 0
    while ((Get-RemainingMilliseconds -Deadline $DeadlineUtc) -gt 0) {
        $discoveryScanCount += 1
        $recordCountBefore = @($Session.OwnedProcesses).Count
        $launcherRecord = @(
            $Session.OwnedProcesses | Where-Object {
                $_.owned -eq $true -and $_.roles -contains "launcher"
            }
        ) | Select-Object -First 1
        if ($null -eq $launcherRecord) {
            throw "Probe discovery lost its exact launcher record."
        }
        $launcherState = Get-OwnedProcessState -Record $launcherRecord
        if ($launcherState.status -eq "alive-owned") {
            $null = @(
                Sync-OwnedDescendants -Session $Session -OwnedRegistry $OwnedRegistry `
                    -DeadlineUtc $DeadlineUtc
            )
            $ownershipDiscoveryPasses += 1
            $launcherState = Get-OwnedProcessState -Record $launcherRecord
        }
        if ($launcherState.status -eq "exited") {
            $rootExitObserved = $true
        }
        elseif ($launcherState.status -ne "alive-owned") {
            throw "Probe launcher identity became unverifiable during discovery: $($launcherState.status)"
        }

        $ownedStates = @(
            $Session.OwnedProcesses |
                Where-Object { $_.owned -eq $true } |
                ForEach-Object { Get-OwnedProcessState -Record $_ }
        )
        $invalidStates = @(
            $ownedStates | Where-Object { $_.status -ne "alive-owned" -and $_.status -ne "exited" }
        )
        if ($invalidStates.Count -gt 0) {
            throw "Probe descendant identity became unverifiable during discovery."
        }
        $allOwnedExited = $ownedStates.Count -gt 0 -and @(
            $ownedStates | Where-Object { $_.status -ne "exited" }
        ).Count -eq 0

        if ($rootExitObserved) {
            $postExitReconciliationPasses += 1
            $recordCountAfter = @($Session.OwnedProcesses).Count
            if ($allOwnedExited -and $recordCountAfter -eq $recordCountBefore) {
                $stablePassesObserved += 1
            }
            else {
                $stablePassesObserved = 0
            }
        }
        if ($rootExitObserved -and $stablePassesObserved -ge $StablePassesRequired) {
            $Session.DiscoveryComplete = $true
            return [pscustomobject]@{
                pass = $true
                rootExitObserved = $true
                allOwnedExitedBeforeCleanup = $true
                discoveryScanCount = $discoveryScanCount
                ownershipDiscoveryPasses = $ownershipDiscoveryPasses
                postExitDiscoveryPasses = $postExitDiscoveryPasses
                postExitReconciliationPasses = $postExitReconciliationPasses
                stablePassesRequired = $StablePassesRequired
                stablePassesObserved = $stablePassesObserved
            }
        }

        $remainingMilliseconds = Get-RemainingMilliseconds -Deadline $DeadlineUtc
        if ($remainingMilliseconds -le 0) {
            break
        }
        Start-Sleep -Milliseconds ([Math]::Min($PollMilliseconds, $remainingMilliseconds))
    }
    throw "Probe topology did not reach stable post-exit reconciliation before its absolute deadline."
}

function Get-ProbeTopologyEvidence {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session,
        [Parameter(Mandatory = $true)]
        [object]$Discovery,
        [Parameter(Mandatory = $true)]
        [bool]$ExpectedRuntimeChild
    )

    $ownedRecords = @($Session.OwnedProcesses | Where-Object { $_.owned -eq $true })
    $launcherRecords = @($ownedRecords | Where-Object { $_.roles -contains "launcher" })
    $pythonDescendants = @(
        $ownedRecords | Where-Object {
            $_.roles -notcontains "launcher" -and
            ($_.imageName -ieq "python.exe" -or $_.imageName -ieq "pythonw.exe")
        }
    )
    if ($ExpectedRuntimeChild -and $pythonDescendants.Count -eq 1) {
        $pythonDescendants[0].roles = @($pythonDescendants[0].roles + @("runtime") | Select-Object -Unique)
    }
    elseif (-not $ExpectedRuntimeChild -and $launcherRecords.Count -eq 1) {
        $launcherRecords[0].roles = @($launcherRecords[0].roles + @("runtime") | Select-Object -Unique)
    }
    $runtimeRecords = @($ownedRecords | Where-Object { $_.roles -contains "runtime" })

    $recordsByIdentity = @{}
    $exactIdentitiesComplete = $true
    foreach ($record in $ownedRecords) {
        if (
            $null -eq $record.startTimeUtcTicks -or [long]$record.startTimeUtcTicks -le 0 -or
            $null -eq $record.process
        ) {
            $exactIdentitiesComplete = $false
        }
        $identityKey = "$([int]$record.pid):$([long]$record.startTimeUtcTicks)"
        if ($recordsByIdentity.ContainsKey($identityKey)) {
            $exactIdentitiesComplete = $false
        }
        $recordsByIdentity[$identityKey] = $record
    }
    $parentChainsRooted = $launcherRecords.Count -eq 1
    if ($parentChainsRooted) {
        [int]$launcherPid = $launcherRecords[0].pid
        foreach ($record in $ownedRecords) {
            if ([int]$record.pid -eq $launcherPid) {
                if ([int]$record.parentPid -ne 0 -or [long]$record.parentStartTimeUtcTicks -ne 0) {
                    $parentChainsRooted = $false
                }
                continue
            }
            $visited = @{}
            $current = $record
            while ([int]$current.pid -ne $launcherPid) {
                $currentKey = "$([int]$current.pid):$([long]$current.startTimeUtcTicks)"
                if (
                    $visited.ContainsKey($currentKey) -or [int]$current.parentPid -le 0 -or
                    $null -eq $current.parentStartTimeUtcTicks -or [long]$current.parentStartTimeUtcTicks -le 0
                ) {
                    $parentChainsRooted = $false
                    break
                }
                $visited[$currentKey] = $true
                $parentKey = "$([int]$current.parentPid):$([long]$current.parentStartTimeUtcTicks)"
                if (-not $recordsByIdentity.ContainsKey($parentKey)) {
                    $parentChainsRooted = $false
                    break
                }
                $current = $recordsByIdentity[$parentKey]
            }
            if (-not $parentChainsRooted) {
                break
            }
        }
    }

    $allOwnedExitedBeforeCleanup = $ownedRecords.Count -gt 0
    foreach ($record in $ownedRecords) {
        if ((Get-OwnedProcessState -Record $record).status -ne "exited") {
            $allOwnedExitedBeforeCleanup = $false
        }
    }
    $observationCount = @($Session.OwnedProcesses | Where-Object { $_.owned -ne $true }).Count
    $runtimeShapeMatches = $runtimeRecords.Count -eq 1
    if ($runtimeShapeMatches -and $launcherRecords.Count -eq 1) {
        $runtimeShapeMatches = if ($ExpectedRuntimeChild) {
            [int]$runtimeRecords[0].pid -ne [int]$launcherRecords[0].pid -and $pythonDescendants.Count -eq 1
        }
        else {
            [int]$runtimeRecords[0].pid -eq [int]$launcherRecords[0].pid -and $pythonDescendants.Count -eq 0
        }
    }
    $pass = $Discovery.pass -eq $true -and $Discovery.rootExitObserved -eq $true -and
        $Discovery.allOwnedExitedBeforeCleanup -eq $true -and
        [int]$Discovery.stablePassesRequired -ge 2 -and
        [int]$Discovery.postExitDiscoveryPasses -eq 0 -and
        [int]$Discovery.stablePassesObserved -ge [int]$Discovery.stablePassesRequired -and
        [int]$Discovery.postExitReconciliationPasses -ge [int]$Discovery.stablePassesRequired -and
        $launcherRecords.Count -eq 1 -and $runtimeShapeMatches -and $parentChainsRooted -and
        $exactIdentitiesComplete -and $allOwnedExitedBeforeCleanup -and $observationCount -eq 0

    return [pscustomobject]@{
        pass = $pass
        criterion = "one exact launcher and one expected runtime role captured while exact parents were alive-owned; every edge binds parent PID plus parent start ticks to a retained identity; no post-exit discovery; no observations; all exact processes exited; at least two unchanged post-exit reconciliations"
        expectedRuntimeChild = $ExpectedRuntimeChild
        launcherCount = $launcherRecords.Count
        runtimeCount = $runtimeRecords.Count
        parentChainsRooted = $parentChainsRooted
        noUnverifiableObservations = $observationCount -eq 0
        observationCount = $observationCount
        rootExitObserved = $Discovery.rootExitObserved
        allOwnedExitedBeforeCleanup = $allOwnedExitedBeforeCleanup
        discoveryScanCount = $Discovery.discoveryScanCount
        ownershipDiscoveryPasses = $Discovery.ownershipDiscoveryPasses
        postExitDiscoveryPasses = $Discovery.postExitDiscoveryPasses
        postExitReconciliationPasses = $Discovery.postExitReconciliationPasses
        stablePassesRequired = $Discovery.stablePassesRequired
        stablePassesObserved = $Discovery.stablePassesObserved
    }
}

function Close-ProbeResources {
    param(
        [object]$Process = $null,
        [object]$StdoutTask = $null,
        [object]$StderrTask = $null,
        [AllowEmptyCollection()]
        [object[]]$OwnedRecords = @()
    )

    $issues = New-Object System.Collections.ArrayList
    if ($null -ne $Process) {
        foreach ($streamName in @("StandardOutput", "StandardError")) {
            try {
                if ($null -ne $Process.PSObject.Properties[$streamName] -and $null -ne $Process.$streamName) {
                    $Process.$streamName.Dispose()
                }
            }
            catch {
                $null = $issues.Add("probe $streamName disposal failed: $($_.Exception.Message)")
            }
        }
    }
    foreach ($taskEntry in @(
            [pscustomobject]@{ name = "stdout task"; value = $StdoutTask },
            [pscustomobject]@{ name = "stderr task"; value = $StderrTask }
        )) {
        if ($null -eq $taskEntry.value) {
            continue
        }
        try {
            if (
                $null -ne $taskEntry.value.PSObject.Properties["IsCompleted"] -and
                $taskEntry.value.IsCompleted -ne $true
            ) {
                $null = $issues.Add("probe $($taskEntry.name) was incomplete at disposal")
            }
            $taskEntry.value.Dispose()
        }
        catch {
            $null = $issues.Add("probe $($taskEntry.name) disposal failed: $($_.Exception.Message)")
        }
    }

    $uniqueProcesses = New-Object System.Collections.ArrayList
    foreach ($candidate in @(
            @($OwnedRecords | Where-Object { $_.owned -eq $true -and $null -ne $_.process } | ForEach-Object { $_.process }) +
            @($Process)
        )) {
        if ($null -eq $candidate) {
            continue
        }
        $alreadyAdded = $false
        foreach ($existing in @($uniqueProcesses)) {
            if ([object]::ReferenceEquals($existing, $candidate)) {
                $alreadyAdded = $true
                break
            }
        }
        if (-not $alreadyAdded) {
            $null = $uniqueProcesses.Add($candidate)
        }
    }
    foreach ($ownedProcess in @($uniqueProcesses)) {
        try {
            $ownedProcess.Dispose()
        }
        catch {
            $null = $issues.Add("probe process-handle disposal failed: $($_.Exception.Message)")
        }
    }
    return [pscustomobject]@{
        pass = $issues.Count -eq 0
        disposedProcessHandleCount = $uniqueProcesses.Count
        issues = [object[]]@($issues)
    }
}

function Invoke-PythonProbe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string]$Code,
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage,
        [ValidateRange(1, 300)]
        [int]$TimeoutSeconds = 30,
        [ValidateRange(1, 30)]
        [int]$CleanupTimeoutSeconds = 5
    )

    $process = $null
    $processStarted = $false
    $localOwnershipEstablished = $false
    $stdoutTask = $null
    $stderrTask = $null
    $probeSession = $null
    $probeOwnedRecords = New-Object System.Collections.ArrayList
    $probeIdentity = $null
    $probeExitCode = $null
    $probeDiscovery = $null
    $probeTopology = $null
    $cleanupResult = $null
    $retainedRootCleanup = $null
    $closeResult = $null
    $primaryException = $null
    $cleanupProblems = New-Object System.Collections.ArrayList
    $stdout = ""
    try {
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $Executable
        $startInfo.Arguments = '-c "{0}"' -f $Code
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "$FailureMessage The interpreter could not be started: $Executable"
        }
        $processStarted = $true
        $localOwnershipEstablished = $true
        $probeSession = New-ProbeOwnershipSession -Process $process -OwnedRegistry $probeOwnedRecords
        $null = Initialize-ProbeOwnershipSession -Session $probeSession
        Open-RetainedProcessHandle -Process $process
        $probeIdentity = Get-ExactProcessIdentity -Process $process
        $null = Register-OwnedProcess -Session $probeSession -Process $process -Role "launcher" `
            -Depth 0 -ImageName ([IO.Path]::GetFileName($Executable)) `
            -OwnedRegistry $probeOwnedRecords
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
        $fiveSecondDiscoveryDeadline = [DateTime]::UtcNow.AddSeconds(5)
        $discoveryDeadline = if ($fiveSecondDiscoveryDeadline -lt $deadline) {
            $fiveSecondDiscoveryDeadline
        }
        else {
            $deadline
        }
        $probeDiscovery = Complete-ProbeDiscovery -Session $probeSession -OwnedRegistry $probeOwnedRecords `
            -DeadlineUtc $discoveryDeadline
        if (-not (Wait-TaskUntilDeadline -Task $stdoutTask -Deadline $deadline)) {
            throw "$FailureMessage The interpreter probe stdout drain timed out."
        }
        if (-not (Wait-TaskUntilDeadline -Task $stderrTask -Deadline $deadline)) {
            throw "$FailureMessage The interpreter probe stderr drain timed out."
        }
        $stdout = $stdoutTask.Result.Trim()
        $stderr = $stderrTask.Result.Trim()
        $probeExitCode = [int]$process.ExitCode
        if ($probeExitCode -ne 0) {
            $detail = if ($stderr) { $stderr } else { "no diagnostic output" }
            throw "$FailureMessage Interpreter: $Executable. Details: $detail"
        }
        $probeParts = $stdout -split '\|', 3
        if ($probeParts.Count -ne 3 -or ($probeParts[2] -ne "0" -and $probeParts[2] -ne "1")) {
            throw "$FailureMessage Interpreter probe output omitted the runtime-topology marker."
        }
        $expectedRuntimeChild = $probeParts[2] -eq "1"
        $probeTopology = Get-ProbeTopologyEvidence -Session $probeSession -Discovery $probeDiscovery `
            -ExpectedRuntimeChild $expectedRuntimeChild
        $probeSession.DiscoveryComplete = $probeTopology.pass -eq $true
        $topologyEntries = @()
        foreach ($record in @($probeSession.OwnedProcesses | Where-Object { $_.owned -eq $true })) {
            $topologyEntries += [pscustomobject]@{
                pid = [int]$record.pid
                parentPid = [int]$record.parentPid
                parentStartTimeUtcTicks = [long]$record.parentStartTimeUtcTicks
                roles = @($record.roles)
                startTimeUtc = [string]$record.startTimeUtc
                startTimeUtcTicks = [long]$record.startTimeUtcTicks
                status = (Get-OwnedProcessState -Record $record).status
            }
        }
        if (-not (Test-ProbeTopologyComplete -Topology $probeTopology -OwnedEntries $topologyEntries)) {
            throw "$FailureMessage Interpreter probe topology was not proven complete."
        }
    }
    catch {
        $primaryException = $_.Exception
    }
    finally {
        if ($null -ne $probeSession) {
            $launcherRecord = @(
                $probeSession.OwnedProcesses | Where-Object {
                    $_.owned -eq $true -and $_.roles -contains "launcher"
                }
            ) | Select-Object -First 1
            if ($null -eq $launcherRecord -and $null -ne $probeIdentity) {
                $launcherRecord = @(
                    $probeOwnedRecords | Where-Object {
                        $_.owned -eq $true -and [object]::ReferenceEquals($_.process, $process) -and
                        $_.pid -eq $probeIdentity.pid -and
                        $_.startTimeUtcTicks -eq $probeIdentity.startTimeUtcTicks
                    }
                ) | Select-Object -First 1
                if ($null -eq $launcherRecord) {
                    $launcherRecord = [pscustomobject]@{
                        backend = $probeSession.Backend
                        session = $probeSession.Index
                        pid = [int]$probeIdentity.pid
                        parentPid = 0
                        parentStartTimeUtcTicks = [long]0
                        depth = 0
                        imageName = [IO.Path]::GetFileName($Executable)
                        roles = @("launcher")
                        owned = $true
                        actionable = $true
                        startTimeUtc = [string]$probeIdentity.startTimeUtc
                        startTimeUtcTicks = [long]$probeIdentity.startTimeUtcTicks
                        discoveredAtUtc = [DateTime]::UtcNow.ToString("o")
                        observationStatus = $null
                        issue = "emergency exact launcher record after registration failure"
                        process = $process
                    }
                    $null = $probeOwnedRecords.Add($launcherRecord)
                }
                if (-not $probeSession.OwnedProcesses.Contains($launcherRecord)) {
                    $null = $probeSession.OwnedProcesses.Add($launcherRecord)
                }
            }
            try {
                if ($null -ne $launcherRecord) {
                    $cleanupResult = Stop-ExactOwnedProcessTree -Session $probeSession `
                        -OwnedRegistry $probeOwnedRecords -DiscoveryTimeoutSeconds 3 `
                        -HandleWaitTimeoutSeconds $CleanupTimeoutSeconds
                    $treeKillResult = $cleanupResult.treeKill
                    $boundedWaitResults = @($cleanupResult.waitResults)
                    if (
                        $null -eq $treeKillResult -or
                        $null -eq $cleanupResult.PSObject.Properties["waitResults"] -or
                        $boundedWaitResults.Count -eq 0 -or
                        $cleanupResult.pass -ne $true
                    ) {
                        $detail = @($cleanupResult.issues) -join "; "
                        if ([string]::IsNullOrWhiteSpace($detail)) {
                            $detail = "cleanup result omitted required tree-kill or bounded-wait proof"
                        }
                        $null = $cleanupProblems.Add($detail)
                    }
                }
                else {
                    $retainedRootCleanup = Stop-RetainedProcessHandle -Process $process `
                        -TimeoutSeconds $CleanupTimeoutSeconds
                    $null = $cleanupProblems.Add(
                        "probe exact topology was not established; retained-root cleanup pass=$($retainedRootCleanup.pass), waitCompleted=$($retainedRootCleanup.waitCompleted), exited=$($retainedRootCleanup.exited), issue=$($retainedRootCleanup.issue)"
                    )
                }
            }
            catch {
                $null = $cleanupProblems.Add("exact probe-tree cleanup threw: $($_.Exception.Message)")
            }
        }
        elseif ($processStarted -and $localOwnershipEstablished -and $null -ne $process) {
            try {
                $retainedRootCleanup = Stop-RetainedProcessHandle -Process $process `
                    -TimeoutSeconds $CleanupTimeoutSeconds
                $null = $cleanupProblems.Add(
                    "probe local ownership session was unavailable; retained-root cleanup pass=$($retainedRootCleanup.pass), waitCompleted=$($retainedRootCleanup.waitCompleted), exited=$($retainedRootCleanup.exited), issue=$($retainedRootCleanup.issue)"
                )
            }
            catch {
                $null = $cleanupProblems.Add("retained-root probe cleanup threw: $($_.Exception.Message)")
            }
        }
        elseif ($processStarted) {
            $null = $cleanupProblems.Add("probe process started before local cleanup ownership could be recorded")
        }

        $drainDeadline = [DateTime]::UtcNow.AddSeconds($CleanupTimeoutSeconds)
        foreach ($drainEntry in @(
                [pscustomobject]@{ name = "stdout"; task = $stdoutTask },
                [pscustomobject]@{ name = "stderr"; task = $stderrTask }
            )) {
            if ($null -eq $drainEntry.task -or $drainEntry.task.IsCompleted) {
                continue
            }
            try {
                if (-not (Wait-TaskUntilDeadline -Task $drainEntry.task -Deadline $drainDeadline)) {
                    $null = $cleanupProblems.Add("probe $($drainEntry.name) drain did not complete during cleanup")
                }
            }
            catch {
                $null = $cleanupProblems.Add("probe $($drainEntry.name) drain cleanup failed: $($_.Exception.Message)")
            }
        }
        try {
            $closeResult = Close-ProbeResources -Process $process -StdoutTask $stdoutTask -StderrTask $stderrTask `
                -OwnedRecords @($probeOwnedRecords)
            foreach ($closeIssue in @($closeResult.issues)) {
                $null = $cleanupProblems.Add($closeIssue)
            }
        }
        catch {
            $null = $cleanupProblems.Add("probe resource disposal threw: $($_.Exception.Message)")
        }
    }

    if ($cleanupProblems.Count -gt 0) {
        $primaryDetail = if ($null -ne $primaryException) { " Primary failure: $($primaryException.Message)" } else { "" }
        throw "$FailureMessage Probe cleanup failed: $(@($cleanupProblems) -join '; ')$primaryDetail"
    }
    if ($null -ne $primaryException) {
        if ($processStarted) {
            $cleanupPass = if ($null -ne $cleanupResult) {
                $cleanupResult.pass
            }
            elseif ($null -ne $retainedRootCleanup) {
                $retainedRootCleanup.pass
            }
            else {
                $false
            }
            $waitComplete = if ($null -ne $cleanupResult) {
                @($cleanupResult.waitResults | Where-Object { -not $_.waitCompleted }).Count -eq 0
            }
            elseif ($null -ne $retainedRootCleanup) {
                $retainedRootCleanup.waitCompleted
            }
            else {
                $false
            }
            $disposalPass = $null -ne $closeResult -and $closeResult.pass -eq $true
            throw "$FailureMessage Probe failed after Start; cleanup pass=$cleanupPass, waitCompleted=$waitComplete, disposalPass=$disposalPass. Primary failure: $($primaryException.Message)"
        }
        throw $primaryException
    }
    $ownedEntries = @()
    foreach ($record in @($probeOwnedRecords | Where-Object { $_.owned -eq $true })) {
        $verificationEntry = @(
            $cleanupResult.verification | Where-Object {
                $_.pid -eq $record.pid -and $_.startTimeUtcTicks -eq $record.startTimeUtcTicks
            }
        ) | Select-Object -First 1
        $ownedEntries += [pscustomobject]@{
            backend = "probe"
            session = 0
            pid = [int]$record.pid
            parentPid = [int]$record.parentPid
            parentStartTimeUtcTicks = [long]$record.parentStartTimeUtcTicks
            imageName = [string]$record.imageName
            roles = @($record.roles)
            owned = $true
            actionable = $true
            startTimeUtc = [string]$record.startTimeUtc
            startTimeUtcTicks = [long]$record.startTimeUtcTicks
            status = if ($null -ne $verificationEntry) { $verificationEntry.status } else { "unreadable" }
            issue = if ($null -ne $verificationEntry) { $verificationEntry.issue } else { "verification entry missing" }
        }
    }
    $probeLifecycle = [pscustomobject]@{
        pass = $cleanupResult.pass -eq $true -and $closeResult.pass -eq $true -and $probeExitCode -eq 0
        exitCode = $probeExitCode
        forcedCleanup = $probeSession.ForcedCleanup
        discoveryComplete = $cleanupResult.discoveryComplete
        ownedCount = $ownedEntries.Count
        ownedEntries = [object[]]@($ownedEntries)
        topology = $probeTopology
        treeKill = $cleanupResult.treeKill
        fallbackAttemptedPids = [int[]]@($cleanupResult.fallbackAttemptedPids)
        waitResults = [object[]]@($cleanupResult.waitResults)
        verification = [object[]]@($cleanupResult.verification)
        resourceDisposal = $closeResult
    }
    if (-not (Test-ProbeLifecycleComplete -Lifecycle $probeLifecycle)) {
        throw "$FailureMessage Probe cleanup evidence was incomplete after successful execution."
    }
    return [pscustomobject]@{
        output = $stdout
        cleanup = $probeLifecycle
    }
}

function Get-PythonRuntimeChild {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$OwnedRegistry
    )

    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ([DateTime]::UtcNow -lt $deadline) {
        $null = @(
            Sync-OwnedDescendants -Session $Session -OwnedRegistry $OwnedRegistry -DeadlineUtc $deadline
        )
        $candidates = @(
            $Session.OwnedProcesses | Where-Object {
                $_.owned -eq $true -and $_.pid -ne $Session.Process.Id -and
                ($_.imageName -ieq "python.exe" -or $_.imageName -ieq "pythonw.exe") -and
                $null -ne $_.process
            }
        )
        $runningCandidates = @()
        foreach ($candidate in $candidates) {
            try {
                $candidate.process.Refresh()
                if (-not $candidate.process.HasExited) {
                    $runningCandidates += $candidate
                }
            }
            catch {
                # Discovery will retry until the absolute child-discovery deadline.
            }
        }
        if ($runningCandidates.Count -eq 1) {
            $runtimeCandidate = $runningCandidates[0]
            $parentRecord = @(
                $Session.OwnedProcesses | Where-Object {
                    $_.owned -eq $true -and $_.pid -eq $runtimeCandidate.parentPid -and
                    $_.startTimeUtcTicks -eq $runtimeCandidate.parentStartTimeUtcTicks
                }
            ) | Select-Object -First 1
            if ($null -eq $parentRecord) {
                throw "$($Session.Backend) session $($Session.Index) runtime child lacked its exact retained parent edge."
            }
            $runtimeRecord = Register-OwnedProcess -Session $Session -Process $runtimeCandidate.process `
                -Role "runtime" -ParentRecord $parentRecord -Depth ([int]$runtimeCandidate.depth) `
                -ImageName ([string]$runningCandidates[0].imageName) -OwnedRegistry $OwnedRegistry
            $null = @(
                Sync-OwnedDescendants -Session $Session -OwnedRegistry $OwnedRegistry -DeadlineUtc $deadline
            )
            return $runtimeRecord.process
        }
        if ($runningCandidates.Count -gt 1) {
            throw "$($Session.Backend) session $($Session.Index) launcher created multiple Python runtime descendants."
        }
        $Session.Process.Refresh()
        if ($Session.Process.HasExited) {
            throw "$($Session.Backend) session $($Session.Index) launcher exited before its Python runtime child was found."
        }
        Start-Sleep -Milliseconds 25
    }
    throw "$($Session.Backend) session $($Session.Index) did not create the expected Python runtime child within 5 seconds."
}

function Register-ActiveSessionOwnership {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$Registry,
        [Parameter(Mandatory = $true)]
        [object]$Session
    )

    if (-not $Registry.Contains($Session)) {
        $null = $Registry.Add($Session)
    }
}

function Close-SessionStartResources {
    param(
        [object]$Session = $null,
        [object]$Process = $null,
        [AllowEmptyCollection()]
        [object[]]$OwnedRecords = @()
    )

    $issues = New-Object System.Collections.ArrayList
    if ($null -ne $Session -and $null -ne $Session.PSObject.Properties["StderrTask"] -and $null -ne $Session.StderrTask) {
        try {
            if (-not $Session.StderrTask.IsCompleted) {
                $null = $issues.Add("measurement start stderr task was incomplete at disposal")
            }
            $Session.StderrTask.Dispose()
        }
        catch {
            $null = $issues.Add("measurement start stderr task disposal failed: $($_.Exception.Message)")
        }
    }
    if ($null -ne $Process) {
        foreach ($streamName in @("StandardInput", "StandardOutput", "StandardError")) {
            try {
                if ($null -ne $Process.PSObject.Properties[$streamName] -and $null -ne $Process.$streamName) {
                    $Process.$streamName.Dispose()
                }
            }
            catch {
                $null = $issues.Add("measurement start $streamName disposal failed: $($_.Exception.Message)")
            }
        }
    }

    $uniqueProcesses = New-Object System.Collections.ArrayList
    foreach ($candidate in @(
            @($OwnedRecords | Where-Object { $_.owned -eq $true -and $null -ne $_.process } | ForEach-Object { $_.process }) +
            @($Process)
        )) {
        if ($null -eq $candidate) {
            continue
        }
        $alreadyAdded = $false
        foreach ($existing in @($uniqueProcesses)) {
            if ([object]::ReferenceEquals($existing, $candidate)) {
                $alreadyAdded = $true
                break
            }
        }
        if (-not $alreadyAdded) {
            $null = $uniqueProcesses.Add($candidate)
        }
    }
    foreach ($ownedProcess in @($uniqueProcesses)) {
        try {
            $ownedProcess.Dispose()
        }
        catch {
            $null = $issues.Add("measurement start process-handle disposal failed: $($_.Exception.Message)")
        }
    }
    return [pscustomobject]@{
        pass = $issues.Count -eq 0
        disposedProcessHandleCount = $uniqueProcesses.Count
        issues = [object[]]@($issues)
    }
}

function Start-McpSession {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Backend,
        [Parameter(Mandatory = $true)]
        [string]$Module,
        [Parameter(Mandatory = $true)]
        [int]$Index,
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory = $true)]
        [string]$SdkPath,
        [Parameter(Mandatory = $true)]
        [bool]$ExpectRuntimeChild,
        [System.Collections.ArrayList]$ProcessRegistry,
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$OwnedRegistry
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Executable
    $startInfo.Arguments = "-m $Module"
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $startInfo.StandardOutputEncoding = $utf8
    $startInfo.StandardErrorEncoding = $utf8

    $sourcePath = Join-Path $WorkingDirectory "src"
    $existingPythonPath = $startInfo.EnvironmentVariables["PYTHONPATH"]
    $startInfo.EnvironmentVariables["PYTHONPATH"] = if ($existingPythonPath) {
        "$sourcePath$([IO.Path]::PathSeparator)$existingPythonPath"
    }
    else {
        $sourcePath
    }
    $startInfo.EnvironmentVariables["EVERYTHING_SDK_DLL"] = $SdkPath
    $startInfo.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
    $startInfo.EnvironmentVariables["PYTHONUNBUFFERED"] = "1"

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $session = [pscustomobject]@{
        Backend = $Backend
        Index = $Index
        Process = $process
        RuntimeProcess = $null
        OwnedRegistry = $OwnedRegistry
        OwnedProcesses = New-Object System.Collections.ArrayList
        StderrTask = $null
        StdoutDrainTask = $null
        StdoutTrailing = ""
        StdoutDrainComplete = $false
        StderrDrainComplete = $false
        ExpectedIds = New-Object System.Collections.ArrayList
        SeenIds = @{}
        Notifications = New-Object System.Collections.ArrayList
        ResponseDeadlineCount = 0
        SentCounts = @{
            initialize = 0
            initializedNotification = 0
            list = 0
            status = 0
            count = 0
            search = 0
        }
        CyclesCompleted = 0
        StatusSuccesses = 0
        CountSuccesses = 0
        SearchSuccesses = 0
        StatusSample = $null
        CountValues = New-Object System.Collections.ArrayList
        SearchItemCounts = New-Object System.Collections.ArrayList
        SearchPaths = New-Object System.Collections.ArrayList
        InputClosed = $false
        ExitCode = $null
        LauncherExitCodeReadable = $false
        HungAtEof = $false
        ForcedCleanup = $false
        Stderr = ""
        StderrClean = $false
        RuntimeExitCode = $null
        RuntimeExitCodeReadable = $false
        LauncherWaitCompleted = $false
        RuntimeWaitCompleted = $false
        DiscoveryComplete = $false
    }
    $processStarted = $false
    $localOwnershipEstablished = $false
    $startSucceeded = $false
    $startFailure = $null
    $startCleanup = $null
    $startClose = $null
    $originalConsoleInputEncoding = [Console]::InputEncoding
    try {
        Set-ConsoleInputEncoding -Encoding $utf8
        if (-not $process.Start()) {
            throw "Failed to start $Backend session $Index."
        }
        $processStarted = $true
        $localOwnershipEstablished = $true
        Set-ConsoleInputEncoding -Encoding $originalConsoleInputEncoding -StartedProcess $process
        Register-ActiveSessionOwnership -Registry $ProcessRegistry -Session $session
        Open-RetainedProcessHandle -Process $process
        $null = Get-ExactProcessIdentity -Process $process
        $null = Register-OwnedProcess -Session $session -Process $process -Role "launcher" -Depth 0 `
            -ImageName ([IO.Path]::GetFileName($Executable)) `
            -OwnedRegistry $OwnedRegistry
        $session.StderrTask = $process.StandardError.ReadToEndAsync()
        if ($ExpectRuntimeChild) {
            $session.RuntimeProcess = Get-PythonRuntimeChild -Session $session -OwnedRegistry $OwnedRegistry
        }
        else {
            $session.RuntimeProcess = $process
            $null = Register-OwnedProcess -Session $session -Process $process -Role "runtime" -Depth 0 `
                -ImageName ([IO.Path]::GetFileName($Executable)) `
                -OwnedRegistry $OwnedRegistry
        }
        $session.DiscoveryComplete = $true
        $startSucceeded = $true
    }
    catch {
        $startFailure = $_.Exception
    }
    finally {
        try {
            Set-ConsoleInputEncoding -Encoding $originalConsoleInputEncoding
        }
        catch {
            if ($null -eq $startFailure) {
                $startFailure = $_.Exception
                $startSucceeded = $false
            }
        }
        if ($processStarted -and $localOwnershipEstablished -and -not $startSucceeded) {
            $launcherRecord = @(
                $session.OwnedProcesses | Where-Object {
                    $_.owned -eq $true -and $_.roles -contains "launcher"
                }
            ) | Select-Object -First 1
            try {
                if ($null -ne $launcherRecord) {
                    $startCleanup = Stop-ExactOwnedProcessTree -Session $session -OwnedRegistry $OwnedRegistry
                }
                else {
                    $startCleanup = Stop-RetainedProcessHandle -Process $process -TimeoutSeconds 5
                    $null = $startCleanup.waitCompleted
                    $null = $startCleanup.exited
                }
            }
            catch {
                $startCleanup = [pscustomobject]@{
                    pass = $false
                    waitCompleted = $false
                    exited = $false
                    issue = $_.Exception.Message
                }
            }
            try {
                $startClose = Close-SessionStartResources -Session $session -Process $process `
                    -OwnedRecords @($session.OwnedProcesses)
            }
            catch {
                $startClose = [pscustomobject]@{
                    pass = $false
                    issues = @($_.Exception.Message)
                }
            }
            if ($ProcessRegistry.Contains($session)) {
                $ProcessRegistry.Remove($session)
            }
        }
    }
    if ($null -ne $startFailure) {
        $cleanupPass = $null -ne $startCleanup -and $startCleanup.pass -eq $true
        $waitCompleted = if (
            $null -ne $startCleanup -and $null -ne $startCleanup.PSObject.Properties["waitResults"]
        ) {
            @($startCleanup.waitResults | Where-Object { -not $_.waitCompleted }).Count -eq 0
        }
        elseif ($null -ne $startCleanup) {
            $startCleanup.waitCompleted -eq $true
        }
        else {
            $false
        }
        $exited = if (
            $null -ne $startCleanup -and $null -ne $startCleanup.PSObject.Properties["verification"]
        ) {
            @($startCleanup.verification | Where-Object { $_.status -ne "exited" }).Count -eq 0
        }
        elseif ($null -ne $startCleanup) {
            $startCleanup.exited -eq $true
        }
        else {
            $false
        }
        $closePass = $null -ne $startClose -and $startClose.pass -eq $true
        throw "$Backend session $Index failed after Start; cleanup pass=$cleanupPass, waitCompleted=$waitCompleted, exited=$exited, disposalPass=$closePass. Primary failure: $($startFailure.Message)"
    }
    return $session
}

function Send-JsonMessage {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session,
        [Parameter(Mandatory = $true)]
        [object]$Message
    )

    $Session.Process.Refresh()
    if ($Session.Process.HasExited) {
        throw "$($Session.Backend) session $($Session.Index) exited before a message could be sent."
    }
    $line = $Message | ConvertTo-Json -Depth 20 -Compress
    $Session.Process.StandardInput.WriteLine($line)
    $Session.Process.StandardInput.Flush()
}

function Assert-ValidNotification {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Message,
        [Parameter(Mandatory = $true)]
        [string]$Context
    )

    if (
        $null -eq $Message -or
        $Message -is [System.Array] -or
        $Message -is [string] -or
        $Message -is [ValueType]
    ) {
        throw "$Context notification was not a JSON object."
    }
    if (-not (Test-ObjectProperty -InputObject $Message -Name "jsonrpc") -or [string]$Message.jsonrpc -ne "2.0") {
        throw "$Context notification did not use jsonrpc 2.0."
    }
    if (
        -not (Test-ObjectProperty -InputObject $Message -Name "method") -or
        -not ($Message.method -is [string]) -or
        [string]::IsNullOrWhiteSpace([string]$Message.method)
    ) {
        throw "$Context notification did not contain a nonempty method."
    }
    foreach ($forbidden in @("id", "result", "error")) {
        if (Test-ObjectProperty -InputObject $Message -Name $forbidden) {
            throw "$Context notification contained forbidden property '$forbidden'."
        }
    }
    if (Test-ObjectProperty -InputObject $Message -Name "params") {
        $params = $Message.params
        if (
            $null -eq $params -or
            $params -is [System.Array] -or
            $params -is [string] -or
            $params -is [ValueType]
        ) {
            throw "$Context notification params was not an object."
        }
    }
}

function Receive-MatchingResponse {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedId,
        [Parameter(Mandatory = $true)]
        [int]$TimeoutSeconds
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    if (Test-ObjectProperty -InputObject $Session -Name "ResponseDeadlineCount") {
        $Session.ResponseDeadlineCount = [int]$Session.ResponseDeadlineCount + 1
    }
    while ($true) {
        $remainingMilliseconds = Get-RemainingMilliseconds -Deadline $deadline
        if ($remainingMilliseconds -le 0) {
            throw "$($Session.Backend) session $($Session.Index) timed out waiting for response $ExpectedId."
        }
        $readTask = $Session.Process.StandardOutput.ReadLineAsync()
        if (-not $readTask.Wait($remainingMilliseconds)) {
            throw "$($Session.Backend) session $($Session.Index) timed out waiting for response $ExpectedId."
        }
        $line = $readTask.Result
        if ($null -eq $line) {
            throw "$($Session.Backend) session $($Session.Index) reached stdout EOF while waiting for $ExpectedId."
        }
        if ([string]::IsNullOrWhiteSpace($line)) {
            throw "$($Session.Backend) session $($Session.Index) emitted an empty stdout protocol line."
        }

        try {
            $message = $line | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            throw "$($Session.Backend) session $($Session.Index) emitted malformed JSON: $line"
        }
        if ($message -is [System.Array] -or -not (Test-ObjectProperty -InputObject $message -Name "jsonrpc")) {
            throw "$($Session.Backend) session $($Session.Index) emitted a non-object JSON-RPC message."
        }
        if ([string]$message.jsonrpc -ne "2.0") {
            throw "$($Session.Backend) session $($Session.Index) emitted jsonrpc '$($message.jsonrpc)' instead of '2.0'."
        }

        $hasMethod = Test-ObjectProperty -InputObject $message -Name "method"
        $hasId = Test-ObjectProperty -InputObject $message -Name "id"
        if ($hasMethod) {
            Assert-ValidNotification -Message $message -Context "$($Session.Backend) session $($Session.Index)"
            $null = $Session.Notifications.Add([string]$message.method)
            continue
        }
        if (-not $hasId) {
            throw "$($Session.Backend) session $($Session.Index) emitted a response without an id."
        }

        $responseId = [string]$message.id
        if ($responseId -ne $ExpectedId) {
            throw "$($Session.Backend) session $($Session.Index) received unexpected or cross-session id '$responseId' while waiting for '$ExpectedId'. Raw line: $line"
        }
        if ($Session.SeenIds.ContainsKey($responseId)) {
            throw "$($Session.Backend) session $($Session.Index) received duplicate response id '$responseId'."
        }
        $Session.SeenIds[$responseId] = 1

        if (Test-ObjectProperty -InputObject $message -Name "error") {
            $errorText = $message.error | ConvertTo-Json -Depth 10 -Compress
            throw "$($Session.Backend) session $($Session.Index) received protocol error for '$responseId': $errorText"
        }
        if (-not (Test-ObjectProperty -InputObject $message -Name "result")) {
            throw "$($Session.Backend) session $($Session.Index) received a response without result or error."
        }
        return $message
    }
}

function Invoke-RequestStage {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$SessionObjects,
        [Parameter(Mandatory = $true)]
        [string]$IdSuffix,
        [Parameter(Mandatory = $true)]
        [string]$Method,
        [Parameter(Mandatory = $true)]
        [string]$CountKey,
        [Parameter(Mandatory = $true)]
        [scriptblock]$ParamsFactory,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Validator,
        [Parameter(Mandatory = $true)]
        [int]$TimeoutSeconds
    )

    $requestIds = @{}
    foreach ($session in $SessionObjects) {
        $requestId = "$($session.Backend)-s$('{0:D2}' -f $session.Index)-$IdSuffix"
        $message = [ordered]@{
            jsonrpc = "2.0"
            id = $requestId
            method = $Method
        }
        $params = & $ParamsFactory $session
        if ($null -ne $params) {
            $message["params"] = $params
        }
        $null = $session.ExpectedIds.Add($requestId)
        $session.SentCounts[$CountKey] = [int]$session.SentCounts[$CountKey] + 1
        $requestIds[$session.Index] = $requestId
        Send-JsonMessage -Session $session -Message $message
    }

    foreach ($session in $SessionObjects) {
        $response = Receive-MatchingResponse -Session $session -ExpectedId $requestIds[$session.Index] -TimeoutSeconds $TimeoutSeconds
        $null = & $Validator $response $session
    }
}

function Assert-InitializeResponse {
    param(
        [object]$Response,
        [object]$Session
    )

    if ([string]$Response.result.protocolVersion -ne "2025-06-18") {
        throw "$($Session.Backend) session $($Session.Index) negotiated unexpected protocol version '$($Response.result.protocolVersion)'."
    }
    if (-not (Test-ObjectProperty -InputObject $Response.result -Name "serverInfo")) {
        throw "$($Session.Backend) session $($Session.Index) initialize result omitted serverInfo."
    }
}

function Assert-ToolsListResponse {
    param(
        [object]$Response,
        [object]$Session
    )

    $tools = @($Response.result.tools)
    $actualNames = @($tools | ForEach-Object { [string]$_.name } | Sort-Object)
    $differences = @(Compare-Object -ReferenceObject $ExpectedToolNames -DifferenceObject $actualNames)
    if ($tools.Count -ne 4 -or $differences.Count -ne 0) {
        throw "$($Session.Backend) session $($Session.Index) returned tools [$($actualNames -join ', ')] instead of the exact four expected tools."
    }
}

function Get-ToolPayload {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Response,
        [Parameter(Mandatory = $true)]
        [object]$Session
    )

    $result = $Response.result
    if (Test-ObjectProperty -InputObject $result -Name "isError") {
        if ([bool]$result.isError) {
            $detail = $result | ConvertTo-Json -Depth 20 -Compress
            throw "$($Session.Backend) session $($Session.Index) received a tool error result: $detail"
        }
    }
    if (-not (Test-ObjectProperty -InputObject $result -Name "content")) {
        throw "$($Session.Backend) session $($Session.Index) tool result omitted content."
    }
    $content = @($result.content)
    if ($content.Count -lt 1 -or [string]$content[0].type -ne "text" -or -not ($content[0].text -is [string])) {
        throw "$($Session.Backend) session $($Session.Index) tool result did not contain nonempty text content."
    }

    if ((Test-ObjectProperty -InputObject $result -Name "structuredContent") -and $null -ne $result.structuredContent) {
        return $result.structuredContent
    }
    try {
        return $content[0].text | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "$($Session.Backend) session $($Session.Index) tool text content was not structured JSON."
    }
}

function Assert-StatusResponse {
    param(
        [object]$Response,
        [object]$Session
    )

    $payload = Get-ToolPayload -Response $Response -Session $Session
    if ($payload.everythingInstalled -ne $true -or $payload.everythingRunning -ne $true) {
        throw "$($Session.Backend) session $($Session.Index) did not report Everything installed and running."
    }
    if ([string]$payload.backend -ne "sdk-ipc") {
        throw "$($Session.Backend) session $($Session.Index) selected backend '$($payload.backend)' instead of sdk-ipc."
    }
    if (-not (Test-ObjectProperty -InputObject $payload -Name "dbLoaded") -or $payload.dbLoaded -ne $true) {
        throw "$($Session.Backend) session $($Session.Index) did not report a loaded SDK database."
    }
    if ($null -eq $Session.StatusSample) {
        $Session.StatusSample = [pscustomobject]@{
            everythingInstalled = [bool]$payload.everythingInstalled
            everythingRunning = [bool]$payload.everythingRunning
            backend = [string]$payload.backend
            dbLoaded = [bool]$payload.dbLoaded
        }
    }
    $Session.StatusSuccesses++
}

function Assert-CountResponse {
    param(
        [object]$Response,
        [object]$Session
    )

    $payload = Get-ToolPayload -Response $Response -Session $Session
    if (-not (Test-ObjectProperty -InputObject $payload -Name "count")) {
        throw "$($Session.Backend) session $($Session.Index) count payload omitted count."
    }
    $rawCount = $payload.count
    if ($null -eq $rawCount -or $rawCount -is [string] -or $rawCount -is [bool] -or -not ($rawCount -is [ValueType])) {
        throw "$($Session.Backend) session $($Session.Index) count was not numeric."
    }
    $numericCount = [long]$rawCount
    if ($numericCount -lt 1) {
        throw "$($Session.Backend) session $($Session.Index) count returned $numericCount; the live query must match at least one path."
    }
    $null = $Session.CountValues.Add($numericCount)
    $Session.CountSuccesses++
}

function Test-PathInsideScope {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$ScopePath
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    $fullScope = [IO.Path]::GetFullPath($ScopePath).TrimEnd([char[]]@('\', '/'))
    $scopePrefix = $fullScope + [IO.Path]::DirectorySeparatorChar
    return $fullPath.Equals($fullScope, [StringComparison]::OrdinalIgnoreCase) -or
        $fullPath.StartsWith($scopePrefix, [StringComparison]::OrdinalIgnoreCase)
}

function Assert-SearchResponse {
    param(
        [object]$Response,
        [object]$Session,
        [string]$ScopePath,
        [bool]$WithMetadata
    )

    $payload = Get-ToolPayload -Response $Response -Session $Session
    $items = @($payload.items)
    if ($items.Count -lt 1) {
        throw "$($Session.Backend) session $($Session.Index) search returned no items."
    }
    foreach ($item in $items) {
        $path = if ($WithMetadata) { [string]$item.path } else { [string]$item }
        if ([string]::IsNullOrWhiteSpace($path)) {
            throw "$($Session.Backend) session $($Session.Index) search returned an empty path."
        }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "$($Session.Backend) session $($Session.Index) search returned a path that does not exist: $path"
        }
        if (-not (Test-PathInsideScope -Path $path -ScopePath $ScopePath)) {
            throw "$($Session.Backend) session $($Session.Index) search returned a path outside scope: $path"
        }
        $null = $Session.SearchPaths.Add([IO.Path]::GetFullPath($path))
    }
    $null = $Session.SearchItemCounts.Add($items.Count)
    $Session.SearchSuccesses++
}

function Get-MemoryCheckpoint {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$SessionObjects,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [int]$Cycle
    )

    $perProcess = @()
    [long]$workingSetTotal = 0
    [long]$privateTotal = 0
    foreach ($session in $SessionObjects) {
        $session.RuntimeProcess.Refresh()
        if ($session.RuntimeProcess.HasExited) {
            throw "$($session.Backend) session $($session.Index) exited before memory checkpoint '$Name'."
        }
        [long]$workingSet = $session.RuntimeProcess.WorkingSet64
        [long]$privateBytes = $session.RuntimeProcess.PrivateMemorySize64
        $workingSetTotal += $workingSet
        $privateTotal += $privateBytes
        $perProcess += [pscustomobject]@{
            session = $session.Index
            pid = $session.RuntimeProcess.Id
            launcherPid = $session.Process.Id
            workingSetBytes = $workingSet
            privateBytes = $privateBytes
        }
    }
    return [pscustomobject]@{
        name = $Name
        cycle = $Cycle
        capturedAtUtc = [DateTime]::UtcNow.ToString("o")
        perProcess = @($perProcess)
        aggregate = [pscustomobject]@{
            workingSetBytes = $workingSetTotal
            privateBytes = $privateTotal
        }
    }
}

function Get-TrailingProtocolIssues {
    param(
        [object]$Session,
        [AllowEmptyString()]
        [string]$TrailingOutput
    )

    $issues = New-Object System.Collections.ArrayList
    $reader = New-Object System.IO.StringReader($TrailingOutput)
    while ($null -ne ($line = $reader.ReadLine())) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            $null = $issues.Add("$($Session.Backend) session $($Session.Index) emitted an empty trailing protocol line")
            continue
        }
        try {
            $message = $line | ConvertFrom-Json -ErrorAction Stop
        }
        catch {
            $null = $issues.Add("$($Session.Backend) session $($Session.Index) emitted malformed trailing JSON")
            continue
        }
        if (
            $message -is [System.Array] -or
            -not (Test-ObjectProperty -InputObject $message -Name "jsonrpc") -or
            [string]$message.jsonrpc -ne "2.0"
        ) {
            $null = $issues.Add("$($Session.Backend) session $($Session.Index) emitted malformed trailing JSON-RPC")
            continue
        }
        $hasMethod = Test-ObjectProperty -InputObject $message -Name "method"
        $hasId = Test-ObjectProperty -InputObject $message -Name "id"
        if ($hasMethod) {
            try {
                Assert-ValidNotification -Message $message -Context "$($Session.Backend) session $($Session.Index) trailing"
                $null = $Session.Notifications.Add([string]$message.method)
            }
            catch {
                $null = $issues.Add($_.Exception.Message)
            }
            continue
        }
        if ($hasId) {
            $responseId = [string]$message.id
            $kind = if ($Session.SeenIds.ContainsKey($responseId)) { "duplicate" } else { "unexpected" }
            $null = $issues.Add("$($Session.Backend) session $($Session.Index) emitted $kind trailing response id '$responseId'")
            continue
        }
        $null = $issues.Add("$($Session.Backend) session $($Session.Index) emitted an invalid trailing protocol message")
    }
    $reader.Dispose()
    return @($issues)
}

function Get-RequiredProcessExitCode {
    param(
        [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if ($null -eq $Process) {
        return [pscustomobject]@{ readable = $false; code = $null; issue = "$Label process was not discovered" }
    }
    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            return [pscustomobject]@{ readable = $false; code = $null; issue = "$Label process had not exited" }
        }
        $rawCode = $Process.ExitCode
        if ($null -eq $rawCode) {
            return [pscustomobject]@{ readable = $false; code = $null; issue = "$Label exit code was null" }
        }
        return [pscustomobject]@{ readable = $true; code = [int]$rawCode; issue = $null }
    }
    catch {
        return [pscustomobject]@{
            readable = $false
            code = $null
            issue = "$Label exit code was unreadable: $($_.Exception.Message)"
        }
    }
}

function Test-SessionExitCodesComplete {
    param([object]$SessionEvidence)

    if ($null -eq $SessionEvidence) {
        return $false
    }
    foreach ($name in @("launcherExitCode", "runtimeExitCode")) {
        if (-not (Test-ObjectProperty -InputObject $SessionEvidence -Name $name)) {
            return $false
        }
        $value = $SessionEvidence.$name
        if ($null -eq $value -or -not ($value -is [int]) -or [int]$value -ne 0) {
            return $false
        }
    }
    return $true
}

function Test-ProbeTopologyComplete {
    param(
        [object]$Topology,
        [AllowEmptyCollection()]
        [object[]]$OwnedEntries = @()
    )

    if ($null -eq $Topology) {
        return $false
    }
    foreach ($name in @(
            "pass", "criterion", "expectedRuntimeChild", "launcherCount", "runtimeCount",
            "parentChainsRooted", "noUnverifiableObservations", "observationCount", "rootExitObserved",
            "allOwnedExitedBeforeCleanup", "discoveryScanCount", "ownershipDiscoveryPasses",
            "postExitDiscoveryPasses", "postExitReconciliationPasses",
            "stablePassesRequired", "stablePassesObserved"
        )) {
        if (-not (Test-ObjectProperty -InputObject $Topology -Name $name) -or $null -eq $Topology.$name) {
            return $false
        }
    }
    foreach ($name in @(
            "launcherCount", "runtimeCount", "observationCount", "discoveryScanCount",
            "ownershipDiscoveryPasses", "postExitDiscoveryPasses",
            "postExitReconciliationPasses", "stablePassesRequired", "stablePassesObserved"
        )) {
        if (-not ($Topology.$name -is [int]) -or [int]$Topology.$name -lt 0) {
            return $false
        }
    }
    if (
        $Topology.pass -ne $true -or
        [string]::IsNullOrWhiteSpace([string]$Topology.criterion) -or
        -not ($Topology.expectedRuntimeChild -is [bool]) -or
        $Topology.parentChainsRooted -ne $true -or
        $Topology.noUnverifiableObservations -ne $true -or
        [int]$Topology.observationCount -ne 0 -or
        $Topology.rootExitObserved -ne $true -or
        $Topology.allOwnedExitedBeforeCleanup -ne $true -or
        [int]$Topology.discoveryScanCount -lt 2 -or
        ([bool]$Topology.expectedRuntimeChild -and [int]$Topology.ownershipDiscoveryPasses -lt 1) -or
        [int]$Topology.postExitDiscoveryPasses -ne 0 -or
        [int]$Topology.stablePassesRequired -lt 2 -or
        [int]$Topology.stablePassesObserved -lt [int]$Topology.stablePassesRequired -or
        [int]$Topology.postExitReconciliationPasses -lt [int]$Topology.stablePassesRequired
    ) {
        return $false
    }

    $entries = @($OwnedEntries)
    $launcherEntries = @($entries | Where-Object { @($_.roles) -contains "launcher" })
    $runtimeEntries = @($entries | Where-Object { @($_.roles) -contains "runtime" })
    if (
        $entries.Count -eq 0 -or
        $launcherEntries.Count -ne 1 -or
        $runtimeEntries.Count -ne 1 -or
        [int]$Topology.launcherCount -ne $launcherEntries.Count -or
        [int]$Topology.runtimeCount -ne $runtimeEntries.Count
    ) {
        return $false
    }
    foreach ($entry in $entries) {
        if (
            $null -eq $entry -or
            -not ($entry.pid -is [int]) -or [int]$entry.pid -le 0 -or
            -not ($entry.parentPid -is [int]) -or
            -not ($entry.parentStartTimeUtcTicks -is [long]) -or
            @($entry.roles).Count -eq 0 -or
            [string]::IsNullOrWhiteSpace([string]$entry.startTimeUtc) -or
            -not ($entry.startTimeUtcTicks -is [long]) -or [long]$entry.startTimeUtcTicks -le 0 -or
            [string]$entry.status -ne "exited"
        ) {
            return $false
        }
    }
    [int]$launcherPid = $launcherEntries[0].pid
    if (
        ([bool]$Topology.expectedRuntimeChild -and [int]$runtimeEntries[0].pid -eq $launcherPid) -or
        (-not [bool]$Topology.expectedRuntimeChild -and [int]$runtimeEntries[0].pid -ne $launcherPid)
    ) {
        return $false
    }

    $entriesByIdentity = @{}
    foreach ($entry in $entries) {
        $key = "$([int]$entry.pid):$([long]$entry.startTimeUtcTicks)"
        if ($entriesByIdentity.ContainsKey($key)) {
            return $false
        }
        $entriesByIdentity[$key] = $entry
    }
    foreach ($entry in $entries) {
        if ([int]$entry.pid -eq $launcherPid) {
            if ([int]$entry.parentPid -ne 0 -or [long]$entry.parentStartTimeUtcTicks -ne 0) {
                return $false
            }
            continue
        }
        $visited = @{}
        $current = $entry
        while ([int]$current.pid -ne $launcherPid) {
            $currentKey = "$([int]$current.pid):$([long]$current.startTimeUtcTicks)"
            if (
                $visited.ContainsKey($currentKey) -or [int]$current.parentPid -le 0 -or
                [long]$current.parentStartTimeUtcTicks -le 0
            ) {
                return $false
            }
            $visited[$currentKey] = $true
            $parentKey = "$([int]$current.parentPid):$([long]$current.parentStartTimeUtcTicks)"
            if (-not $entriesByIdentity.ContainsKey($parentKey)) {
                return $false
            }
            $current = $entriesByIdentity[$parentKey]
        }
    }
    return $true
}

function Test-ProbeLifecycleComplete {
    param([object]$Lifecycle)

    if ($null -eq $Lifecycle) {
        return $false
    }
    foreach ($name in @(
            "pass", "exitCode", "forcedCleanup", "discoveryComplete", "ownedCount", "ownedEntries",
            "topology", "treeKill", "fallbackAttemptedPids", "waitResults", "verification", "resourceDisposal"
        )) {
        if (-not (Test-ObjectProperty -InputObject $Lifecycle -Name $name) -or $null -eq $Lifecycle.$name) {
            return $false
        }
    }
    if (
        $Lifecycle.pass -ne $true -or
        -not ($Lifecycle.exitCode -is [int]) -or
        [int]$Lifecycle.exitCode -ne 0 -or
        $Lifecycle.forcedCleanup -ne $false -or
        $Lifecycle.discoveryComplete -ne $true -or
        -not ($Lifecycle.ownedCount -is [int]) -or
        [int]$Lifecycle.ownedCount -le 0 -or
        $Lifecycle.treeKill.pass -ne $true -or
        $Lifecycle.treeKill.waitCompleted -ne $true -or
        $Lifecycle.resourceDisposal.pass -ne $true
    ) {
        return $false
    }
    $ownedEntries = @($Lifecycle.ownedEntries)
    $waitResults = @($Lifecycle.waitResults)
    $verification = @($Lifecycle.verification)
    if (
        -not (Test-ProbeTopologyComplete -Topology $Lifecycle.topology -OwnedEntries $ownedEntries) -or
        $ownedEntries.Count -ne [int]$Lifecycle.ownedCount -or
        $waitResults.Count -ne $ownedEntries.Count -or
        $verification.Count -ne $ownedEntries.Count -or
        @($Lifecycle.fallbackAttemptedPids).Count -ne 0 -or
        -not ($Lifecycle.resourceDisposal.disposedProcessHandleCount -is [int]) -or
        [int]$Lifecycle.resourceDisposal.disposedProcessHandleCount -ne $ownedEntries.Count
    ) {
        return $false
    }
    foreach ($entry in $ownedEntries) {
        if (
            $null -eq $entry -or
            -not ($entry.pid -is [int]) -or
            [int]$entry.pid -le 0 -or
            [string]::IsNullOrWhiteSpace([string]$entry.startTimeUtc) -or
            -not ($entry.startTimeUtcTicks -is [long]) -or
            [long]$entry.startTimeUtcTicks -le 0 -or
            [string]$entry.status -ne "exited"
        ) {
            return $false
        }
    }
    foreach ($waitResult in $waitResults) {
        if ($null -eq $waitResult -or $waitResult.waitCompleted -ne $true) {
            return $false
        }
    }
    foreach ($verificationEntry in $verification) {
        if (
            $null -eq $verificationEntry -or
            -not ($verificationEntry.startTimeUtcTicks -is [long]) -or
            [long]$verificationEntry.startTimeUtcTicks -le 0 -or
            [string]$verificationEntry.status -ne "exited"
        ) {
            return $false
        }
    }
    return $true
}

function Test-ProcessAuditComplete {
    param([object]$Audit)

    if ($null -eq $Audit) {
        return $false
    }
    foreach ($name in @(
            "pass", "checkedPids", "checkedCount", "alivePids", "aliveCount", "reusedPids", "reusedCount",
            "unreadablePids", "unreadableCount", "ownedCount", "observationCount", "ownedIdentitiesComplete",
            "ownedEdgesComplete", "entries"
        )) {
        if (-not (Test-ObjectProperty -InputObject $Audit -Name $name)) {
            return $false
        }
    }
    foreach ($name in @("checkedPids", "alivePids", "reusedPids", "unreadablePids", "entries")) {
        if ($null -eq $Audit.$name) {
            return $false
        }
    }
    foreach ($name in @("checkedCount", "aliveCount", "reusedCount", "unreadableCount", "ownedCount", "observationCount")) {
        if (-not ($Audit.$name -is [int]) -or [int]$Audit.$name -lt 0) {
            return $false
        }
    }
    $checkedPids = @($Audit.checkedPids)
    $alivePids = @($Audit.alivePids)
    $reusedPids = @($Audit.reusedPids)
    $unreadablePids = @($Audit.unreadablePids)
    $entries = @($Audit.entries)
    if (
        $Audit.pass -ne $true -or
        $Audit.ownedIdentitiesComplete -ne $true -or
        $Audit.ownedEdgesComplete -ne $true -or
        $checkedPids.Count -eq 0 -or
        [int]$Audit.checkedCount -ne $checkedPids.Count -or
        [int]$Audit.checkedCount -ne $entries.Count -or
        [int]$Audit.aliveCount -ne $alivePids.Count -or
        [int]$Audit.reusedCount -ne $reusedPids.Count -or
        [int]$Audit.unreadableCount -ne $unreadablePids.Count -or
        $alivePids.Count -ne 0 -or
        $reusedPids.Count -ne 0 -or
        $unreadablePids.Count -ne 0 -or
        [int]$Audit.ownedCount -le 0 -or
        [int]$Audit.ownedCount + [int]$Audit.observationCount -ne $entries.Count
    ) {
        return $false
    }
    for ($index = 0; $index -lt $entries.Count; $index++) {
        $entry = $entries[$index]
        if (
            $null -eq $entry -or
            -not (Test-ObjectProperty -InputObject $entry -Name "pid") -or
            -not ($entry.pid -is [int]) -or
            [int]$entry.pid -le 0 -or
            -not ($checkedPids[$index] -is [int]) -or
            [int]$checkedPids[$index] -ne [int]$entry.pid
        ) {
            return $false
        }
        if ($entry.owned -eq $true) {
            if (
                $entry.actionable -ne $true -or
                [string]::IsNullOrWhiteSpace([string]$entry.startTimeUtc) -or
                -not ($entry.startTimeUtcTicks -is [long]) -or
                [long]$entry.startTimeUtcTicks -le 0 -or
                -not ($entry.parentPid -is [int]) -or
                -not ($entry.parentStartTimeUtcTicks -is [long]) -or
                [string]$entry.status -ne "exited"
            ) {
                return $false
            }
        }
    }
    return $true
}

function Test-IndependentPidRecheckComplete {
    param([object]$Recheck)

    if ($null -eq $Recheck) {
        return $false
    }
    foreach ($name in @(
            "pass", "checkedPids", "checkedCount", "aliveOwnedPids", "aliveOwnedCount", "reusedPids", "reusedCount",
            "unreadablePids", "unreadableCount", "entries"
        )) {
        if (-not (Test-ObjectProperty -InputObject $Recheck -Name $name)) {
            return $false
        }
    }
    foreach ($name in @("checkedPids", "aliveOwnedPids", "reusedPids", "unreadablePids", "entries")) {
        if ($null -eq $Recheck.$name) {
            return $false
        }
    }
    foreach ($name in @("checkedCount", "aliveOwnedCount", "reusedCount", "unreadableCount")) {
        if (-not ($Recheck.$name -is [int]) -or [int]$Recheck.$name -lt 0) {
            return $false
        }
    }
    $checkedPids = @($Recheck.checkedPids)
    $alivePids = @($Recheck.aliveOwnedPids)
    $reusedPids = @($Recheck.reusedPids)
    $unreadablePids = @($Recheck.unreadablePids)
    $entries = @($Recheck.entries)
    if (
        $Recheck.pass -ne $true -or
        $checkedPids.Count -eq 0 -or
        [int]$Recheck.checkedCount -ne $checkedPids.Count -or
        [int]$Recheck.checkedCount -ne $entries.Count -or
        [int]$Recheck.aliveOwnedCount -ne $alivePids.Count -or
        [int]$Recheck.reusedCount -ne $reusedPids.Count -or
        [int]$Recheck.unreadableCount -ne $unreadablePids.Count -or
        $alivePids.Count -ne 0 -or
        $reusedPids.Count -ne 0 -or
        $unreadablePids.Count -ne 0
    ) {
        return $false
    }
    for ($index = 0; $index -lt $entries.Count; $index++) {
        $entry = $entries[$index]
        if (
            $null -eq $entry -or
            -not ($entry.pid -is [int]) -or
            [int]$entry.pid -le 0 -or
            -not ($checkedPids[$index] -is [int]) -or
            [int]$checkedPids[$index] -ne [int]$entry.pid -or
            -not ($entry.recordedStartTimeUtcTicks -is [long]) -or
            [long]$entry.recordedStartTimeUtcTicks -le 0 -or
            [string]$entry.status -ne "not-present"
        ) {
            return $false
        }
    }
    return $true
}

function Test-AllExplicitPassComponents {
    param([object]$Components)

    if ($null -eq $Components) {
        return $false
    }
    $required = @(
        "liteProtocolAccountingCleanup",
        "fastMcpProtocolAccountingCleanup",
        "postRunPidAudit",
        "probeLifecycleCleanup",
        "independentPidRecheck",
        "exitCodeCompleteness",
        "memoryEvidenceValidation",
        "memoryStability",
        "liteFastMcpIdleRatio",
        "evidenceIntegrity"
    )
    foreach ($name in $required) {
        if (-not (Test-ObjectProperty -InputObject $Components -Name $name) -or $Components.$name -ne $true) {
            return $false
        }
    }
    return $true
}

function Complete-SessionProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$SessionObjects,
        [Parameter(Mandatory = $true)]
        [int]$TimeoutSeconds
    )

    $issues = New-Object System.Collections.ArrayList
    foreach ($session in $SessionObjects) {
        if (-not $session.InputClosed) {
            try {
                $session.Process.StandardInput.Close()
            }
            finally {
                $session.InputClosed = $true
            }
        }
        try {
            $session.StdoutDrainTask = $session.Process.StandardOutput.ReadToEndAsync()
        }
        catch {
            $null = $issues.Add("$($session.Backend) session $($session.Index) could not start stdout drain: $($_.Exception.Message)")
        }
    }

    foreach ($session in $SessionObjects) {
        $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
        try {
            $session.LauncherWaitCompleted = Wait-ProcessUntilDeadline -Process $session.Process -Deadline $deadline
        }
        catch {
            $session.LauncherWaitCompleted = $false
            $null = $issues.Add("$($session.Backend) session $($session.Index) launcher wait failed: $($_.Exception.Message)")
        }
        if (-not $session.LauncherWaitCompleted) {
            $session.HungAtEof = $true
            $null = $issues.Add("$($session.Backend) session $($session.Index) launcher hung after stdin EOF")
        }
        if ($null -eq $session.RuntimeProcess) {
            $session.RuntimeWaitCompleted = $false
            $null = $issues.Add("$($session.Backend) session $($session.Index) runtime process was not discovered")
        }
        else {
            try {
                $session.RuntimeWaitCompleted = Wait-ProcessUntilDeadline -Process $session.RuntimeProcess -Deadline $deadline
            }
            catch {
                $session.RuntimeWaitCompleted = $false
                $null = $issues.Add("$($session.Backend) session $($session.Index) runtime wait failed: $($_.Exception.Message)")
            }
        }
        if (-not $session.RuntimeWaitCompleted) {
            $session.HungAtEof = $true
            $null = $issues.Add("$($session.Backend) session $($session.Index) runtime hung after stdin EOF")
        }

        if (-not $session.LauncherWaitCompleted -or -not $session.RuntimeWaitCompleted) {
            $cleanupResult = Stop-ExactOwnedProcessTree -Session $session -OwnedRegistry $session.OwnedRegistry
            foreach ($cleanupIssue in @($cleanupResult.issues)) {
                $null = $issues.Add("$($session.Backend) session $($session.Index) $cleanupIssue")
            }
        }

        $launcherExit = Get-RequiredProcessExitCode -Process $session.Process `
            -Label "$($session.Backend) session $($session.Index) launcher"
        $session.LauncherExitCodeReadable = $launcherExit.readable
        $session.ExitCode = $launcherExit.code
        if (-not $launcherExit.readable) {
            $null = $issues.Add($launcherExit.issue)
        }
        elseif ($launcherExit.code -ne 0) {
            $null = $issues.Add("$($session.Backend) session $($session.Index) launcher exited with code $($launcherExit.code)")
        }

        $runtimeExit = Get-RequiredProcessExitCode -Process $session.RuntimeProcess `
            -Label "$($session.Backend) session $($session.Index) runtime"
        $session.RuntimeExitCodeReadable = $runtimeExit.readable
        $session.RuntimeExitCode = $runtimeExit.code
        if (-not $runtimeExit.readable) {
            $null = $issues.Add($runtimeExit.issue)
        }
        elseif ($runtimeExit.code -ne 0) {
            $null = $issues.Add("$($session.Backend) session $($session.Index) runtime exited with code $($runtimeExit.code)")
        }

        if ($null -eq $session.StdoutDrainTask) {
            $null = $issues.Add("$($session.Backend) session $($session.Index) stdout drain task was missing")
        }
        else {
            try {
                $session.StdoutDrainComplete = Wait-TaskUntilDeadline -Task $session.StdoutDrainTask -Deadline $deadline
                if ($session.StdoutDrainComplete) {
                    $session.StdoutTrailing = $session.StdoutDrainTask.Result
                }
                else {
                    $null = $issues.Add("$($session.Backend) session $($session.Index) stdout drain timed out")
                }
            }
            catch {
                $session.StdoutDrainComplete = $false
                $null = $issues.Add("$($session.Backend) session $($session.Index) stdout drain failed: $($_.Exception.Message)")
            }
        }
        try {
            $session.StderrDrainComplete = Wait-TaskUntilDeadline -Task $session.StderrTask -Deadline $deadline
            if ($session.StderrDrainComplete) {
                $session.Stderr = $session.StderrTask.Result
            }
            else {
                $null = $issues.Add("$($session.Backend) session $($session.Index) stderr drain timed out")
            }
        }
        catch {
            $session.StderrDrainComplete = $false
            $null = $issues.Add("$($session.Backend) session $($session.Index) stderr drain failed: $($_.Exception.Message)")
        }
        $session.StderrClean = $session.Stderr -notmatch "(?im)traceback|unhandled exception|\berror\b"
        if (-not $session.StderrClean) {
            $null = $issues.Add("$($session.Backend) session $($session.Index) emitted an error or traceback on stderr")
        }
        if ($session.StdoutDrainComplete) {
            foreach ($protocolIssue in @(Get-TrailingProtocolIssues -Session $session -TrailingOutput $session.StdoutTrailing)) {
                $null = $issues.Add($protocolIssue)
            }
        }
    }
    $allExited = @($SessionObjects | Where-Object {
            -not $_.LauncherWaitCompleted -or -not $_.RuntimeWaitCompleted
        }).Count -eq 0
    $exitCodesComplete = @($SessionObjects | Where-Object {
            $sessionEvidence = [pscustomobject]@{
                launcherExitCode = $_.ExitCode
                runtimeExitCode = $_.RuntimeExitCode
            }
            -not (Test-SessionExitCodesComplete -SessionEvidence $sessionEvidence)
        }).Count -eq 0
    $drainsComplete = @($SessionObjects | Where-Object {
            -not $_.StdoutDrainComplete -or -not $_.StderrDrainComplete
        }).Count -eq 0
    $noForcedCleanup = @($SessionObjects | Where-Object { $_.ForcedCleanup }).Count -eq 0
    return [pscustomobject]@{
        pass = $issues.Count -eq 0 -and $allExited -and $exitCodesComplete -and $drainsComplete -and $noForcedCleanup
        allExited = $allExited
        exitCodesComplete = $exitCodesComplete
        drainsComplete = $drainsComplete
        noForcedCleanup = $noForcedCleanup
        hungCount = @($SessionObjects | Where-Object { $_.HungAtEof }).Count
        forcedCleanupCount = @($SessionObjects | Where-Object { $_.ForcedCleanup }).Count
        exitTimeoutSeconds = $TimeoutSeconds
        finitePipeDrains = $true
        issues = @($issues)
    }
}

function Stop-SessionProcessesForCleanup {
    param([object[]]$SessionObjects)

    foreach ($session in $SessionObjects) {
        if (-not $session.InputClosed) {
            try {
                $session.Process.StandardInput.Close()
            }
            catch {
                # The stream may already be unavailable after a startup or protocol failure.
            }
            $session.InputClosed = $true
        }
    }
    foreach ($session in $SessionObjects) {
        try {
            $null = Stop-ExactOwnedProcessTree -Session $session -OwnedRegistry $session.OwnedRegistry
        }
        catch {
            # Final cleanup remains limited to this session's exact launcher tree and recorded PIDs.
        }
    }
}

function Assert-SessionAccounting {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$SessionObjects,
        [Parameter(Mandatory = $true)]
        [int]$ExpectedCycles
    )

    $globalIds = @{}
    [int]$expectedPerSession = 1 + (4 * $ExpectedCycles)
    foreach ($session in $SessionObjects) {
        $expectedResponseCount = $expectedPerSession
        if ($session.ExpectedIds.Count -ne $expectedResponseCount -or $session.SeenIds.Count -ne $expectedResponseCount) {
            throw "$($session.Backend) session $($session.Index) saw $($session.SeenIds.Count) of $expectedResponseCount expected responses."
        }
        foreach ($requestId in $session.ExpectedIds) {
            if (-not $session.SeenIds.ContainsKey([string]$requestId)) {
                throw "$($session.Backend) session $($session.Index) missed response id '$requestId'."
            }
            if ($globalIds.ContainsKey([string]$requestId)) {
                throw "$($session.Backend) reused response id '$requestId' across sessions."
            }
            $globalIds[[string]$requestId] = 1
        }
        if ($session.SentCounts.initialize -ne 1 -or $session.SentCounts.initializedNotification -ne 1) {
            throw "$($session.Backend) session $($session.Index) did not receive initialize and initialized exactly once."
        }
        foreach ($key in @("list", "status", "count", "search")) {
            if ($session.SentCounts[$key] -ne $ExpectedCycles) {
                throw "$($session.Backend) session $($session.Index) sent $key $($session.SentCounts[$key]) times instead of $ExpectedCycles."
            }
        }
        if (
            $session.CyclesCompleted -ne $ExpectedCycles -or
            $session.StatusSuccesses -ne $ExpectedCycles -or
            $session.CountSuccesses -ne $ExpectedCycles -or
            $session.SearchSuccesses -ne $ExpectedCycles
        ) {
            throw "$($session.Backend) session $($session.Index) did not complete all validated workflows."
        }
    }
    return [pscustomobject]@{
        pass = $true
        idsIsolated = $true
        exactResponseIds = $true
        expectedResponsesPerSession = $expectedPerSession
        expectedResponsesTotal = $expectedPerSession * $SessionObjects.Count
        seenResponsesTotal = $globalIds.Count
        sessionsValidated = $SessionObjects.Count
        cyclesValidatedPerSession = $ExpectedCycles
        issues = @()
    }
}

function Convert-SessionEvidence {
    param([object]$Session)

    $stderrLines = @($Session.Stderr -split "`r?`n" | Where-Object { $_ })
    $runtimePid = if ($null -eq $Session.RuntimeProcess) { $null } else { [int]$Session.RuntimeProcess.Id }
    $runtimeExited = if ($null -eq $Session.RuntimeProcess) { $false } else { [bool]$Session.RuntimeProcess.HasExited }
    return [pscustomobject]@{
        session = $Session.Index
        pid = $runtimePid
        launcherPid = $Session.Process.Id
        cyclesCompleted = $Session.CyclesCompleted
        statusSuccesses = $Session.StatusSuccesses
        countSuccesses = $Session.CountSuccesses
        searchSuccesses = $Session.SearchSuccesses
        statusSample = $Session.StatusSample
        countValues = @($Session.CountValues)
        searchItemCounts = @($Session.SearchItemCounts)
        responseIdsExpected = $Session.ExpectedIds.Count
        responseIdsSeen = $Session.SeenIds.Count
        absoluteResponseDeadlinesUsed = $Session.ResponseDeadlineCount
        unsolicitedNotifications = @($Session.Notifications)
        sent = [pscustomobject]$Session.SentCounts
        sampleSearchPaths = @($Session.SearchPaths | Select-Object -Unique -First 5)
        launcherExitCode = $Session.ExitCode
        runtimeExitCode = $Session.RuntimeExitCode
        launcherExitCodeReadable = $Session.LauncherExitCodeReadable
        runtimeExitCodeReadable = $Session.RuntimeExitCodeReadable
        launcherExited = $Session.Process.HasExited
        runtimeExited = $runtimeExited
        launcherWaitCompleted = $Session.LauncherWaitCompleted
        runtimeWaitCompleted = $Session.RuntimeWaitCompleted
        hungAtEof = $Session.HungAtEof
        forcedCleanup = $Session.ForcedCleanup
        stdoutDrainComplete = $Session.StdoutDrainComplete
        stderrDrainComplete = $Session.StderrDrainComplete
        stderrClean = $Session.StderrClean
        stderrLineCount = $stderrLines.Count
        stderr = $Session.Stderr.Trim()
        ownedPids = @($Session.OwnedProcesses | ForEach-Object {
                [pscustomobject]@{
                    pid = [int]$_.pid
                    parentPid = [int]$_.parentPid
                    parentStartTimeUtcTicks = [long]$_.parentStartTimeUtcTicks
                    imageName = [string]$_.imageName
                    roles = @($_.roles)
                    owned = [bool]$_.owned
                    actionable = [bool]$_.actionable
                    startTimeUtc = $_.startTimeUtc
                    startTimeUtcTicks = $_.startTimeUtcTicks
                    observationStatus = $_.observationStatus
                }
            })
    }
}

function Invoke-BackendMeasurement {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Backend,
        [Parameter(Mandatory = $true)]
        [string]$Module,
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory = $true)]
        [string]$SdkPath,
        [Parameter(Mandatory = $true)]
        [string]$ScopePath,
        [Parameter(Mandatory = $true)]
        [string]$SearchQuery,
        [Parameter(Mandatory = $true)]
        [int]$ProcessCount,
        [Parameter(Mandatory = $true)]
        [int]$CycleCount,
        [Parameter(Mandatory = $true)]
        [int]$IdleDurationSeconds,
        [Parameter(Mandatory = $true)]
        [int]$SearchLimit,
        [Parameter(Mandatory = $true)]
        [string]$SearchSort,
        [Parameter(Mandatory = $true)]
        [bool]$SearchMetadata,
        [Parameter(Mandatory = $true)]
        [int]$ResponseTimeout,
        [Parameter(Mandatory = $true)]
        [int]$ExitTimeout,
        [Parameter(Mandatory = $true)]
        [bool]$ExpectRuntimeChild,
        [System.Collections.ArrayList]$ProcessRegistry,
        [AllowEmptyCollection()]
        [System.Collections.ArrayList]$OwnedRegistry
    )

    Write-Host "Starting $ProcessCount $Backend processes ($Module)..."
    $sessionObjects = @()
    $completedNormally = $false
    try {
        for ($index = 1; $index -le $ProcessCount; $index++) {
            $sessionObjects += Start-McpSession -Backend $Backend -Module $Module -Index $index -Executable $Executable `
                -WorkingDirectory $WorkingDirectory -SdkPath $SdkPath -ExpectRuntimeChild $ExpectRuntimeChild `
                -ProcessRegistry $ProcessRegistry -OwnedRegistry $OwnedRegistry
        }
        if ($sessionObjects.Count -ne $ProcessCount) {
            throw "$Backend started $($sessionObjects.Count) processes instead of $ProcessCount."
        }

        $initializeParams = {
            param($unusedSession)
            return [ordered]@{
                protocolVersion = "2025-06-18"
                capabilities = @{}
                clientInfo = [ordered]@{
                    name = "everything-mew-memory-measurement"
                    version = "1"
                }
            }
        }
        Invoke-RequestStage -SessionObjects $sessionObjects -IdSuffix "initialize" -Method "initialize" `
            -CountKey "initialize" -ParamsFactory $initializeParams -Validator ${function:Assert-InitializeResponse} `
            -TimeoutSeconds $ResponseTimeout

        foreach ($session in $sessionObjects) {
            Send-JsonMessage -Session $session -Message ([ordered]@{
                    jsonrpc = "2.0"
                    method = "notifications/initialized"
                })
            $session.SentCounts.initializedNotification = [int]$session.SentCounts.initializedNotification + 1
        }
        Start-Sleep -Milliseconds 250

        $memory = [ordered]@{}
        $memory["initialized"] = Get-MemoryCheckpoint -SessionObjects $sessionObjects -Name "initialized" -Cycle 0

        $noParams = { param($unusedSession) return $null }
        $toolListValidator = ${function:Assert-ToolsListResponse}
        $statusParams = {
            param($unusedSession)
            return [ordered]@{
                name = "everything_status"
                arguments = @{}
            }
        }
        $countParams = {
            param($unusedSession)
            return [ordered]@{
                name = "everything_count"
                arguments = [ordered]@{
                    query = $SearchQuery
                    scope = $ScopePath
                }
            }
        }
        $searchParams = {
            param($unusedSession)
            return [ordered]@{
                name = "everything_search"
                arguments = [ordered]@{
                    query = $SearchQuery
                    scope = $ScopePath
                    limit = $SearchLimit
                    sort = $SearchSort
                    metadata = $SearchMetadata
                }
            }
        }
        $searchValidator = {
            param($response, $session)
            Assert-SearchResponse -Response $response -Session $session -ScopePath $ScopePath -WithMetadata $SearchMetadata
        }

        for ($cycle = 1; $cycle -le $CycleCount; $cycle++) {
            $cycleLabel = "cycle-$('{0:D2}' -f $cycle)"
            Invoke-RequestStage -SessionObjects $sessionObjects -IdSuffix "$cycleLabel-list" -Method "tools/list" `
                -CountKey "list" -ParamsFactory $noParams -Validator $toolListValidator -TimeoutSeconds $ResponseTimeout
            Invoke-RequestStage -SessionObjects $sessionObjects -IdSuffix "$cycleLabel-status" -Method "tools/call" `
                -CountKey "status" -ParamsFactory $statusParams -Validator ${function:Assert-StatusResponse} `
                -TimeoutSeconds $ResponseTimeout
            Invoke-RequestStage -SessionObjects $sessionObjects -IdSuffix "$cycleLabel-count" -Method "tools/call" `
                -CountKey "count" -ParamsFactory $countParams -Validator ${function:Assert-CountResponse} `
                -TimeoutSeconds $ResponseTimeout
            Invoke-RequestStage -SessionObjects $sessionObjects -IdSuffix "$cycleLabel-search" -Method "tools/call" `
                -CountKey "search" -ParamsFactory $searchParams -Validator $searchValidator -TimeoutSeconds $ResponseTimeout

            foreach ($session in $sessionObjects) {
                $session.CyclesCompleted++
            }
            if ($cycle -eq 1) {
                $memory["afterFirstSearch"] = Get-MemoryCheckpoint -SessionObjects $sessionObjects -Name "afterFirstSearch" -Cycle 1
            }
            if ($cycle -eq $CycleCount) {
                $memory["afterFinalSearch"] = Get-MemoryCheckpoint -SessionObjects $sessionObjects -Name "afterFinalSearch" -Cycle $cycle
            }
        }

        Write-Host "$Backend completed $($ProcessCount * $CycleCount) workflows; idling for $IdleDurationSeconds seconds..."
        Start-Sleep -Seconds $IdleDurationSeconds
        $memory["postIdle"] = Get-MemoryCheckpoint -SessionObjects $sessionObjects -Name "postIdle" -Cycle $CycleCount

        $accounting = Assert-SessionAccounting -SessionObjects $sessionObjects -ExpectedCycles $CycleCount
        $cleanup = Complete-SessionProcesses -SessionObjects $sessionObjects -TimeoutSeconds $ExitTimeout
        if (-not $cleanup.pass) {
            throw "$Backend cleanup failed: $($cleanup.issues -join '; ')"
        }
        $completedNormally = $true

        $protocol = [pscustomobject]@{
            pass = $accounting.pass -eq $true
            version = "2025-06-18"
            responseDeadlineMode = "absolute"
            responseTimeoutSeconds = $ResponseTimeout
            notificationsValidated = $true
            trailingNotificationsValidated = $true
            malformedOutputFails = $true
            stagesSentToAllBeforeCollection = $true
        }

        return [pscustomobject]@{
            backend = $Backend
            module = $Module
            processCount = $ProcessCount
            cyclesPerProcess = $CycleCount
            workflowsCompleted = ($sessionObjects | Measure-Object -Property CyclesCompleted -Sum).Sum
            realSearchSuccesses = ($sessionObjects | Measure-Object -Property SearchSuccesses -Sum).Sum
            idsIsolated = $accounting.idsIsolated
            exactResponseIds = $accounting.exactResponseIds
            protocolVersion = "2025-06-18"
            protocol = $protocol
            accounting = $accounting
            memory = [pscustomobject]$memory
            cleanup = $cleanup
            processes = @($sessionObjects | ForEach-Object { Convert-SessionEvidence -Session $_ })
        }
    }
    finally {
        if (-not $completedNormally) {
            Stop-SessionProcessesForCleanup -SessionObjects $sessionObjects
        }
    }
}

function Test-BackendProtocolAccountingCleanup {
    param(
        [object]$BackendRun,
        [int]$ExpectedSessions,
        [int]$ExpectedCycles
    )

    if ($null -eq $BackendRun) {
        return $false
    }
    foreach ($name in @("protocol", "accounting", "cleanup", "processes", "workflowsCompleted", "realSearchSuccesses")) {
        if (-not (Test-ObjectProperty -InputObject $BackendRun -Name $name) -or $null -eq $BackendRun.$name) {
            return $false
        }
    }
    if (
        $BackendRun.protocol.pass -ne $true -or
        [string]$BackendRun.protocol.responseDeadlineMode -ne "absolute" -or
        $BackendRun.accounting.pass -ne $true -or
        $BackendRun.accounting.idsIsolated -ne $true -or
        $BackendRun.accounting.exactResponseIds -ne $true -or
        $BackendRun.cleanup.pass -ne $true -or
        $BackendRun.cleanup.allExited -ne $true -or
        $BackendRun.cleanup.exitCodesComplete -ne $true -or
        $BackendRun.cleanup.drainsComplete -ne $true -or
        $BackendRun.cleanup.noForcedCleanup -ne $true
    ) {
        return $false
    }
    if (
        [int]$BackendRun.processCount -ne $ExpectedSessions -or
        [int]$BackendRun.cyclesPerProcess -ne $ExpectedCycles -or
        [int]$BackendRun.workflowsCompleted -ne ($ExpectedSessions * $ExpectedCycles) -or
        [int]$BackendRun.realSearchSuccesses -ne ($ExpectedSessions * $ExpectedCycles) -or
        @($BackendRun.processes).Count -ne $ExpectedSessions
    ) {
        return $false
    }
    foreach ($session in @($BackendRun.processes)) {
        if (
            -not (Test-SessionExitCodesComplete -SessionEvidence $session) -or
            $session.launcherExitCodeReadable -ne $true -or
            $session.runtimeExitCodeReadable -ne $true -or
            $session.launcherWaitCompleted -ne $true -or
            $session.runtimeWaitCompleted -ne $true -or
            $session.stdoutDrainComplete -ne $true -or
            $session.stderrDrainComplete -ne $true -or
            $session.forcedCleanup -ne $false -or
            $session.stderrClean -ne $true -or
            [int]$session.cyclesCompleted -ne $ExpectedCycles -or
            [int]$session.searchSuccesses -ne $ExpectedCycles
        ) {
            return $false
        }
    }
    return $true
}

function ConvertTo-RequiredPositiveInt64 {
    param(
        [Parameter(Mandatory = $true)]
        [object]$InputObject,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Context
    )

    if (-not (Test-ObjectProperty -InputObject $InputObject -Name $Name)) {
        throw "$Context omitted required integer '$Name'."
    }
    $value = $InputObject.$Name
    if ($null -eq $value) {
        throw "$Context required integer '$Name' was null."
    }
    $integerTypes = @(
        [sbyte], [byte], [int16], [uint16], [int32], [uint32], [int64], [uint64]
    )
    $isInteger = $false
    foreach ($integerType in $integerTypes) {
        if ($value -is $integerType) {
            $isInteger = $true
            break
        }
    }
    if (-not $isInteger) {
        throw "$Context required integer '$Name' had invalid type '$($value.GetType().FullName)'."
    }
    try {
        [long]$number = [Convert]::ToInt64($value, [Globalization.CultureInfo]::InvariantCulture)
    }
    catch {
        throw "$Context required integer '$Name' was outside Int64 range."
    }
    if ($number -le 0) {
        throw "$Context required integer '$Name' was not positive: $number."
    }
    return $number
}

function Assert-ValidMemoryCheckpoint {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Checkpoint,
        [Parameter(Mandatory = $true)]
        [string]$CheckpointName,
        [Parameter(Mandatory = $true)]
        [int]$ExpectedSessions,
        [Parameter(Mandatory = $true)]
        [string]$Backend
    )

    $context = "$Backend $CheckpointName checkpoint"
    foreach ($name in @("aggregate", "perProcess")) {
        if (-not (Test-ObjectProperty -InputObject $Checkpoint -Name $name) -or $null -eq $Checkpoint.$name) {
            throw "$context omitted required '$name' evidence."
        }
    }
    $perProcess = @($Checkpoint.perProcess)
    if ($perProcess.Count -ne $ExpectedSessions) {
        throw "$context contained $($perProcess.Count) per-process rows instead of $ExpectedSessions."
    }

    $seenSessions = @{}
    $validatedRows = @()
    [long]$workingSetSum = 0
    [long]$privateSum = 0
    foreach ($entry in $perProcess) {
        if ($null -eq $entry) {
            throw "$context contained a null per-process row."
        }
        [long]$sessionValue = ConvertTo-RequiredPositiveInt64 -InputObject $entry -Name "session" -Context $context
        if ($sessionValue -gt $ExpectedSessions) {
            throw "$context contained unexpected session id $sessionValue."
        }
        $sessionKey = [string]$sessionValue
        if ($seenSessions.ContainsKey($sessionKey)) {
            throw "$context contained duplicate session id $sessionValue."
        }
        $seenSessions[$sessionKey] = $true
        [long]$workingSet = ConvertTo-RequiredPositiveInt64 -InputObject $entry -Name "workingSetBytes" `
            -Context "$context session $sessionValue"
        [long]$privateBytes = ConvertTo-RequiredPositiveInt64 -InputObject $entry -Name "privateBytes" `
            -Context "$context session $sessionValue"
        if ([long]::MaxValue - $workingSetSum -lt $workingSet -or [long]::MaxValue - $privateSum -lt $privateBytes) {
            throw "$context per-process sum exceeded Int64 range."
        }
        $workingSetSum += $workingSet
        $privateSum += $privateBytes
        $validatedRows += [pscustomobject]@{
            session = [int]$sessionValue
            workingSetBytes = $workingSet
            privateBytes = $privateBytes
        }
    }
    for ($session = 1; $session -le $ExpectedSessions; $session++) {
        if (-not $seenSessions.ContainsKey([string]$session)) {
            throw "$context omitted session id $session."
        }
    }

    [long]$aggregateWorkingSet = ConvertTo-RequiredPositiveInt64 -InputObject $Checkpoint.aggregate `
        -Name "workingSetBytes" -Context "$context aggregate"
    [long]$aggregatePrivate = ConvertTo-RequiredPositiveInt64 -InputObject $Checkpoint.aggregate `
        -Name "privateBytes" -Context "$context aggregate"
    if ($aggregateWorkingSet -ne $workingSetSum) {
        throw "$context aggregate working set $aggregateWorkingSet did not equal per-process sum $workingSetSum."
    }
    if ($aggregatePrivate -ne $privateSum) {
        throw "$context aggregate private bytes $aggregatePrivate did not equal per-process sum $privateSum."
    }
    [int[]]$sessionIds = @($seenSessions.Keys | ForEach-Object { [int]$_ } | Sort-Object)
    return [pscustomobject]@{
        pass = $true
        checkpoint = $CheckpointName
        expectedSessions = $ExpectedSessions
        perProcessCount = $perProcess.Count
        sessionIds = $sessionIds
        perProcess = [object[]]@($validatedRows | Sort-Object session)
        aggregateWorkingSetBytes = $aggregateWorkingSet
        aggregatePrivateBytes = $aggregatePrivate
        perProcessWorkingSetSum = $workingSetSum
        perProcessPrivateSum = $privateSum
    }
}

function Assert-ValidMemoryEvidence {
    param(
        [Parameter(Mandatory = $true)]
        [object]$BackendRun,
        [Parameter(Mandatory = $true)]
        [int]$ExpectedSessions
    )

    if (-not (Test-ObjectProperty -InputObject $BackendRun -Name "memory") -or $null -eq $BackendRun.memory) {
        throw "$($BackendRun.backend) omitted memory evidence."
    }
    $validations = @()
    foreach ($checkpointName in @("afterFirstSearch", "postIdle")) {
        if (
            -not (Test-ObjectProperty -InputObject $BackendRun.memory -Name $checkpointName) -or
            $null -eq $BackendRun.memory.$checkpointName
        ) {
            throw "$($BackendRun.backend) omitted required memory checkpoint '$checkpointName'."
        }
        $validations += Assert-ValidMemoryCheckpoint -Checkpoint $BackendRun.memory.$checkpointName `
            -CheckpointName $checkpointName -ExpectedSessions $ExpectedSessions -Backend ([string]$BackendRun.backend)
    }
    return [pscustomobject]@{
        backend = [string]$BackendRun.backend
        pass = $true
        expectedSessions = $ExpectedSessions
        checkpoints = [object[]]@($validations)
    }
}

function Get-StabilityGate {
    param(
        [Parameter(Mandatory = $true)]
        [object]$BackendRun,
        [Parameter(Mandatory = $true)]
        [int]$ProcessCount,
        [object]$MemoryValidation = $null
    )

    if ($null -eq $MemoryValidation) {
        $MemoryValidation = Assert-ValidMemoryEvidence -BackendRun $BackendRun -ExpectedSessions $ProcessCount
    }
    if ($MemoryValidation.pass -ne $true -or [int]$MemoryValidation.expectedSessions -ne $ProcessCount) {
        throw "$($BackendRun.backend) memory validation was incomplete."
    }
    $firstMatches = @($MemoryValidation.checkpoints | Where-Object { $_.checkpoint -eq "afterFirstSearch" })
    $idleMatches = @($MemoryValidation.checkpoints | Where-Object { $_.checkpoint -eq "postIdle" })
    if ($firstMatches.Count -ne 1 -or $idleMatches.Count -ne 1) {
        throw "$($BackendRun.backend) memory validation did not contain exactly one required checkpoint of each kind."
    }
    $first = $firstMatches[0]
    $idle = $idleMatches[0]
    $perProcess = @()
    $allPerProcessPass = $true
    for ($index = 0; $index -lt $ProcessCount; $index++) {
        [long]$plateau = $first.perProcess[$index].privateBytes
        [long]$postIdle = $idle.perProcess[$index].privateBytes
        [long]$twentyPercent = [Math]::Ceiling($plateau * 0.20)
        [long]$allowance = [Math]::Max($twentyPercent, $PerProcessFloorBytes)
        [long]$maximum = $plateau + $allowance
        $pass = $postIdle -le $maximum
        if (-not $pass) {
            $allPerProcessPass = $false
        }
        $perProcess += [pscustomobject]@{
            session = $index + 1
            plateauPrivateBytes = $plateau
            postIdlePrivateBytes = $postIdle
            twentyPercentBytes = $twentyPercent
            floorBytes = [long]$PerProcessFloorBytes
            allowanceBytes = $allowance
            maximumBytes = $maximum
            deltaBytes = $postIdle - $plateau
            pass = $pass
        }
    }

    [long]$aggregatePlateau = $first.aggregatePrivateBytes
    [long]$aggregateIdle = $idle.aggregatePrivateBytes
    [long]$aggregateTwentyPercent = [Math]::Ceiling($aggregatePlateau * 0.20)
    [long]$aggregateFloor = $ProcessCount * $PerProcessFloorBytes
    [long]$aggregateAllowance = [Math]::Max($aggregateTwentyPercent, $aggregateFloor)
    [long]$aggregateMaximum = $aggregatePlateau + $aggregateAllowance
    $aggregatePass = $aggregateIdle -le $aggregateMaximum

    return [pscustomobject]@{
        backend = $BackendRun.backend
        memoryValidationPass = $MemoryValidation.pass
        formula = "postIdle <= plateau + max(20% of plateau, 10 MiB per process)"
        perProcess = @($perProcess)
        aggregate = [pscustomobject]@{
            plateauPrivateBytes = $aggregatePlateau
            postIdlePrivateBytes = $aggregateIdle
            twentyPercentBytes = $aggregateTwentyPercent
            floorBytes = $aggregateFloor
            allowanceBytes = $aggregateAllowance
            maximumBytes = $aggregateMaximum
            deltaBytes = $aggregateIdle - $aggregatePlateau
            pass = $aggregatePass
        }
        pass = $allPerProcessPass -and $aggregatePass
    }
}

function Get-GitValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = @(& git -C $WorkingDirectory @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $($output -join ' ')"
    }
    $value = ($output -join "`n").Trim()
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "git $($Arguments -join ' ') returned no value."
    }
    return $value
}

function Get-EvidenceIntegrity {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath
    )

    $scriptSha256 = (Get-FileHash -LiteralPath $ScriptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $gitHead = Get-GitValue -WorkingDirectory $WorkingDirectory -Arguments @("rev-parse", "HEAD")
    $gitTree = Get-GitValue -WorkingDirectory $WorkingDirectory -Arguments @("rev-parse", "HEAD^{tree}")
    $headScriptBlob = Get-GitValue -WorkingDirectory $WorkingDirectory `
        -Arguments @("rev-parse", "HEAD:scripts/measure_lite_sessions.ps1")
    $workingScriptBlob = Get-GitValue -WorkingDirectory $WorkingDirectory `
        -Arguments @("hash-object", "--", $ScriptPath)
    $matchesHead = $headScriptBlob -eq $workingScriptBlob
    return [pscustomobject]@{
        pass = -not [string]::IsNullOrWhiteSpace($scriptSha256) -and
            -not [string]::IsNullOrWhiteSpace($gitHead) -and
            -not [string]::IsNullOrWhiteSpace($gitTree) -and
            $matchesHead
        scriptRelativePath = "scripts/measure_lite_sessions.ps1"
        scriptSha256 = $scriptSha256
        gitHead = $gitHead
        gitTree = $gitTree
        headScriptBlob = $headScriptBlob
        workingScriptBlob = $workingScriptBlob
        scriptMatchesHead = $matchesHead
    }
}

function Format-MiB {
    param([long]$Bytes)
    return ([double]$Bytes / $MiB).ToString("F2", [Globalization.CultureInfo]::InvariantCulture)
}

function New-MarkdownSummary {
    param([object]$Evidence)

    $lines = New-Object System.Collections.Generic.List[string]
    $null = $lines.Add("# Everything_Mew multi-session memory evidence")
    $null = $lines.Add("")
    $null = $lines.Add("- Generated UTC: ``$($Evidence.generatedAtUtc)``")
    $null = $lines.Add("- Python: ``$($Evidence.environment.pythonVersion)``")
    $null = $lines.Add("- FastMCP: ``$($Evidence.environment.fastMcpVersion)``")
    $null = $lines.Add("- Sessions: ``$($Evidence.config.sessions)``; cycles per session: ``$($Evidence.config.cycles)``; idle seconds per backend: ``$($Evidence.config.idleSeconds)``")
    $null = $lines.Add("- Query: ``$($Evidence.config.query)``; scope: ``$($Evidence.config.scope)``; limit: ``$($Evidence.config.limit)``; sort: ``$($Evidence.config.sort)``; metadata: ``$($Evidence.config.metadata)``")
    $null = $lines.Add("- Response deadline: ``$($Evidence.config.responseDeadlineMode)`` with ``$($Evidence.config.responseTimeoutSeconds)`` seconds per expected response; exit/drain timeout: ``$($Evidence.config.exitTimeoutSeconds)`` seconds per session.")
    $null = $lines.Add("- CIM process queries: one absolute discovery deadline, explicit operation timeout capped at ``$($Evidence.config.cimOperationTimeoutMaximumSeconds)`` seconds; runtime discovery ``$($Evidence.config.runtimeDiscoveryDeadlineSeconds)`` seconds; cleanup discovery ``$($Evidence.config.cleanupDiscoveryDeadlineSeconds)`` seconds.")
    $null = $lines.Add("")
    $null = $lines.Add("## Integrity")
    $null = $lines.Add("")
    $null = $lines.Add("- Script SHA-256: ``$($Evidence.integrity.scriptSha256)``")
    $null = $lines.Add("- Git HEAD: ``$($Evidence.integrity.gitHead)``; tree: ``$($Evidence.integrity.gitTree)``")
    $null = $lines.Add("- Script blob at HEAD: ``$($Evidence.integrity.headScriptBlob)``; measured blob: ``$($Evidence.integrity.workingScriptBlob)``; match/pass: ``$($Evidence.integrity.scriptMatchesHead)``/``$($Evidence.integrity.pass)``")
    $null = $lines.Add("- Independent PID recheck JSON: ``$([IO.Path]::GetFileName($Evidence.pidRecheckPath))``")
    $null = $lines.Add("")
    $null = $lines.Add("## Pre-run probe lifecycle")
    $null = $lines.Add("")
    $probeFallback = if (@($Evidence.probeLifecycle.fallbackAttemptedPids).Count) { @($Evidence.probeLifecycle.fallbackAttemptedPids) -join ", " } else { "none" }
    $null = $lines.Add("- Exit code: ``$($Evidence.probeLifecycle.exitCode)``; exact identities: ``$($Evidence.probeLifecycle.ownedCount)``; discovery complete: ``$($Evidence.probeLifecycle.discoveryComplete)``; forced cleanup: ``$($Evidence.probeLifecycle.forcedCleanup)``.")
    $null = $lines.Add("- Topology completeness criterion: $($Evidence.probeLifecycle.topology.criterion); ownership discovery passes while exact parents were alive: ``$($Evidence.probeLifecycle.topology.ownershipDiscoveryPasses)``; post-exit discovery passes: ``$($Evidence.probeLifecycle.topology.postExitDiscoveryPasses)``; stable post-exit reconciliations: ``$($Evidence.probeLifecycle.topology.stablePassesObserved)``/``$($Evidence.probeLifecycle.topology.stablePassesRequired)``; rooted parent chains: ``$($Evidence.probeLifecycle.topology.parentChainsRooted)``; observations: ``$($Evidence.probeLifecycle.topology.observationCount)``.")
    $null = $lines.Add("- Tree cleanup pass/invoked/wait: ``$($Evidence.probeLifecycle.treeKill.pass)``/``$($Evidence.probeLifecycle.treeKill.invoked)``/``$($Evidence.probeLifecycle.treeKill.waitCompleted)``; retained-handle fallback PIDs: ``$probeFallback``.")
    $null = $lines.Add("- Bounded waits: ``$(@($Evidence.probeLifecycle.waitResults).Count)``; exact exit verifications: ``$(@($Evidence.probeLifecycle.verification).Count)``; disposed unique process handles: ``$($Evidence.probeLifecycle.resourceDisposal.disposedProcessHandleCount)``; pass: ``$($Evidence.probeLifecycle.pass)``.")
    $null = $lines.Add("")
    $null = $lines.Add("## Protocol and cleanup")
    $null = $lines.Add("")
    $null = $lines.Add("| Backend | Workflows | Real searches | Protocol | Accounting | Cleanup | Exit codes | Drains | Forced cleanup |")
    $null = $lines.Add("| --- | ---: | ---: | --- | --- | --- | --- | --- | ---: |")
    foreach ($run in @($Evidence.backends)) {
        $null = $lines.Add("| $($run.backend) | $($run.workflowsCompleted) | $($run.realSearchSuccesses) | $($run.protocol.pass) | $($run.accounting.pass) | $($run.cleanup.pass) | $($run.cleanup.exitCodesComplete) | $($run.cleanup.drainsComplete) | $($run.cleanup.forcedCleanupCount) |")
    }
    $null = $lines.Add("")
    $null = $lines.Add("Every status response was validated as installed/running with backend ``sdk-ipc`` and a loaded database. Every count was numeric and positive; every search returned an existing file inside the configured scope.")
    $null = $lines.Add("")
    $null = $lines.Add("| Backend | Session | Launcher PID/code | Runtime PID/code | Launcher/runtime wait | Stdout/stderr drain | EOF hang | Forced cleanup |")
    $null = $lines.Add("| --- | ---: | --- | --- | --- | --- | --- | --- |")
    foreach ($run in @($Evidence.backends)) {
        foreach ($process in @($run.processes)) {
            $null = $lines.Add("| $($run.backend) | $($process.session) | $($process.launcherPid) / $($process.launcherExitCode) | $($process.pid) / $($process.runtimeExitCode) | $($process.launcherWaitCompleted) / $($process.runtimeWaitCompleted) | $($process.stdoutDrainComplete) / $($process.stderrDrainComplete) | $($process.hungAtEof) | $($process.forcedCleanup) |")
        }
    }
    $null = $lines.Add("")
    $null = $lines.Add("## Exact PID audit")
    $null = $lines.Add("")
    $checkedPids = @($Evidence.processAudit.checkedPids) -join ", "
    $alivePids = if (@($Evidence.processAudit.alivePids).Count) { @($Evidence.processAudit.alivePids) -join ", " } else { "none" }
    $reusedPids = if (@($Evidence.processAudit.reusedPids).Count) { @($Evidence.processAudit.reusedPids) -join ", " } else { "none" }
    $unreadablePids = if (@($Evidence.processAudit.unreadablePids).Count) { @($Evidence.processAudit.unreadablePids) -join ", " } else { "none" }
    $null = $lines.Add("- Checked exact owned PIDs: ``$checkedPids``")
    $null = $lines.Add("- Counts: checked ``$($Evidence.processAudit.checkedCount)``; owned ``$($Evidence.processAudit.ownedCount)``; observations ``$($Evidence.processAudit.observationCount)``; complete exact identities ``$($Evidence.processAudit.ownedIdentitiesComplete)``; complete exact parent edges ``$($Evidence.processAudit.ownedEdgesComplete)``.")
    $null = $lines.Add("- Alive owned PIDs: ``$alivePids``; reused PIDs: ``$reusedPids``; unreadable PIDs: ``$unreadablePids``; pass: ``$($Evidence.processAudit.pass)``")
    $null = $lines.Add("- Ownership is based on retained ``System.Diagnostics.Process`` handles and exact non-null UTC start-time ticks captured at discovery; cleanup never reopens a PID for termination.")
    $null = $lines.Add("")
    $null = $lines.Add("| Backend | Session | PID | Exact parent PID/start ticks | Roles | Image | Recorded start UTC/ticks | Audit status |")
    $null = $lines.Add("| --- | ---: | ---: | ---: | --- | --- | --- | --- |")
    foreach ($entry in @($Evidence.processAudit.entries)) {
        $roles = @($entry.roles) -join ","
        $null = $lines.Add("| $($entry.backend) | $($entry.session) | $($entry.pid) | $($entry.parentPid) / $($entry.parentStartTimeUtcTicks) | $roles | $($entry.imageName) | $($entry.startTimeUtc) / $($entry.startTimeUtcTicks) | $($entry.status) |")
    }
    $null = $lines.Add("")
    $null = $lines.Add("### Independent exact-PID recheck")
    $null = $lines.Add("")
    $recheckAlive = if (@($Evidence.independentPidRecheck.aliveOwnedPids).Count) { @($Evidence.independentPidRecheck.aliveOwnedPids) -join ", " } else { "none" }
    $recheckReused = if (@($Evidence.independentPidRecheck.reusedPids).Count) { @($Evidence.independentPidRecheck.reusedPids) -join ", " } else { "none" }
    $recheckUnreadable = if (@($Evidence.independentPidRecheck.unreadablePids).Count) { @($Evidence.independentPidRecheck.unreadablePids) -join ", " } else { "none" }
    $null = $lines.Add("- Checked identities/PID entries: ``$($Evidence.independentPidRecheck.checkedCount)``; alive: ``$recheckAlive``; reused: ``$recheckReused``; unreadable: ``$recheckUnreadable``; pass: ``$($Evidence.independentPidRecheck.pass)``.")
    $null = $lines.Add("")
    $null = $lines.Add("## Memory")
    $null = $lines.Add("")
    $null = $lines.Add("Required first-search and post-idle memory evidence was validated before arithmetic: positive integer values, exactly $($Evidence.config.sessions) unique sessions per checkpoint, and aggregate values equal to per-process sums. Validation pass: ``$($Evidence.memoryValidation.pass)``.")
    $null = $lines.Add("")
    $null = $lines.Add("| Backend | Checkpoint | Aggregate working set bytes | Aggregate private bytes | Per-process private bytes |")
    $null = $lines.Add("| --- | --- | ---: | ---: | --- |")
    foreach ($run in @($Evidence.backends)) {
        foreach ($checkpointName in @("initialized", "afterFirstSearch", "afterFinalSearch", "postIdle")) {
            $checkpoint = $run.memory.$checkpointName
            $perProcess = @($checkpoint.perProcess | ForEach-Object { "$($_.privateBytes) ($(Format-MiB -Bytes $_.privateBytes) MiB)" }) -join "; "
            $null = $lines.Add("| $($run.backend) | $checkpointName | $($checkpoint.aggregate.workingSetBytes) | $($checkpoint.aggregate.privateBytes) | $perProcess |")
        }
    }
    $null = $lines.Add("")
    $null = $lines.Add("## Gates")
    $null = $lines.Add("")
    $ratio = $Evidence.gates.liteIdleVsFastMcp.ratio.ToString("F6", [Globalization.CultureInfo]::InvariantCulture)
    $null = $lines.Add("- Lite idle private ratio: ``$($Evidence.gates.liteIdleVsFastMcp.liteIdlePrivateBytes) / $($Evidence.gates.liteIdleVsFastMcp.fastMcpIdlePrivateBytes) = $ratio``; required ``<= 0.5``; pass: ``$($Evidence.gates.liteIdleVsFastMcp.pass)``.")
    foreach ($gate in @($Evidence.gates.stability)) {
        $aggregate = $gate.aggregate
        $null = $lines.Add("- $($gate.backend) aggregate stability: ``$($aggregate.postIdlePrivateBytes) <= $($aggregate.plateauPrivateBytes) + max($($aggregate.twentyPercentBytes), $($aggregate.floorBytes)) = $($aggregate.maximumBytes)``; pass: ``$($aggregate.pass)``.")
        foreach ($processGate in @($gate.perProcess)) {
            $null = $lines.Add("- $($gate.backend) session $($processGate.session): ``$($processGate.postIdlePrivateBytes) <= $($processGate.plateauPrivateBytes) + max($($processGate.twentyPercentBytes), $($processGate.floorBytes)) = $($processGate.maximumBytes)``; pass: ``$($processGate.pass)``.")
        }
    }
    $null = $lines.Add("")
    $null = $lines.Add("## Overall contract")
    $null = $lines.Add("")
    foreach ($component in @($Evidence.overallComponents.PSObject.Properties)) {
        $null = $lines.Add("- $($component.Name): ``$($component.Value)``")
    }
    $null = $lines.Add("")
    $null = $lines.Add("Overall pass: **$($Evidence.overallPass)**")
    return $lines -join "`n"
}

function Stop-AllRegisteredChildren {
    param([System.Collections.ArrayList]$ProcessRegistry)

    $registered = @($ProcessRegistry)
    if ($registered.Count -gt 0) {
        Stop-SessionProcessesForCleanup -SessionObjects $registered
    }
}

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$repoRootPath = Resolve-ExistingPath -Path $RepoRoot -Description "Repository root"
if (-not (Test-Path -LiteralPath (Join-Path $repoRootPath "src\everything_mcp") -PathType Container)) {
    throw "Repository root does not contain src\everything_mcp: $repoRootPath"
}

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $repoRootPath ".venv\Scripts\python.exe"
}
$pythonExecutable = Resolve-ExistingPath -Path $PythonPath -Description "Python interpreter" -Leaf
$sdkPath = Resolve-ExistingPath -Path $SdkDll -Description "Everything SDK DLL" -Leaf

if ([string]::IsNullOrWhiteSpace($Scope)) {
    $Scope = $repoRootPath
}
$scopePath = Resolve-ExistingPath -Path $Scope -Description "Search scope"

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $repoRootPath "_nonrelease\local-evidence"
}
elseif (-not [IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $repoRootPath $OutputDirectory
}
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)
$null = New-Item -ItemType Directory -Path $outputPath -Force
$integrity = Get-EvidenceIntegrity -WorkingDirectory $repoRootPath -ScriptPath $PSCommandPath

$startedAtUtc = [DateTime]::UtcNow
try {
    $probe = Invoke-PythonProbe -Executable $pythonExecutable `
        -Code "import fastmcp, os, platform, sys, time; print(platform.python_version() + '|' + str(getattr(fastmcp, '__version__', 'unknown')) + '|' + ('1' if os.path.normcase(sys.executable) != os.path.normcase(getattr(sys, '_base_executable', sys.executable)) else '0'), flush=True); time.sleep(1)" `
        -FailureMessage "FastMCP is required for the comparison. Install the project server extra into the selected interpreter."
    $probeLifecycle = $probe.cleanup
    $probeParts = ([string]$probe.output) -split '\|', 3
    $pythonVersion = $probeParts[0]
    $fastMcpVersion = if ($probeParts.Count -gt 1) { $probeParts[1] } else { "unknown" }
    $expectRuntimeChild = $probeParts.Count -gt 2 -and $probeParts[2] -eq "1"

    $lite = Invoke-BackendMeasurement -Backend "lite" -Module "everything_mcp.lite_stdio" -Executable $pythonExecutable `
        -WorkingDirectory $repoRootPath -SdkPath $sdkPath -ScopePath $scopePath -SearchQuery $Query `
        -ProcessCount $Sessions -CycleCount $Cycles -IdleDurationSeconds $IdleSeconds -SearchLimit $Limit `
        -SearchSort $Sort -SearchMetadata ([bool]$Metadata) -ResponseTimeout $ResponseTimeoutSeconds `
        -ExitTimeout $ExitTimeoutSeconds -ExpectRuntimeChild $expectRuntimeChild -ProcessRegistry $ActiveSessions `
        -OwnedRegistry $OwnedProcessRecords

    $fastMcp = Invoke-BackendMeasurement -Backend "fastmcp" -Module "everything_mcp" -Executable $pythonExecutable `
        -WorkingDirectory $repoRootPath -SdkPath $sdkPath -ScopePath $scopePath -SearchQuery $Query `
        -ProcessCount $Sessions -CycleCount $Cycles -IdleDurationSeconds $IdleSeconds -SearchLimit $Limit `
        -SearchSort $Sort -SearchMetadata ([bool]$Metadata) -ResponseTimeout $ResponseTimeoutSeconds `
        -ExitTimeout $ExitTimeoutSeconds -ExpectRuntimeChild $expectRuntimeChild -ProcessRegistry $ActiveSessions `
        -OwnedRegistry $OwnedProcessRecords

    $liteMemoryValidation = Assert-ValidMemoryEvidence -BackendRun $lite -ExpectedSessions $Sessions
    $fastMcpMemoryValidation = Assert-ValidMemoryEvidence -BackendRun $fastMcp -ExpectedSessions $Sessions
    $memoryValidation = [pscustomobject]@{
        pass = $liteMemoryValidation.pass -eq $true -and $fastMcpMemoryValidation.pass -eq $true
        expectedSessionsPerBackend = $Sessions
        backends = @($liteMemoryValidation, $fastMcpMemoryValidation)
    }
    $liteStability = Get-StabilityGate -BackendRun $lite -ProcessCount $Sessions `
        -MemoryValidation $liteMemoryValidation
    $fastMcpStability = Get-StabilityGate -BackendRun $fastMcp -ProcessCount $Sessions `
        -MemoryValidation $fastMcpMemoryValidation
    $liteIdleCheckpoints = @($liteMemoryValidation.checkpoints | Where-Object { $_.checkpoint -eq "postIdle" })
    $fastMcpIdleCheckpoints = @($fastMcpMemoryValidation.checkpoints | Where-Object { $_.checkpoint -eq "postIdle" })
    if ($liteIdleCheckpoints.Count -ne 1 -or $fastMcpIdleCheckpoints.Count -ne 1) {
        throw "Validated memory evidence did not contain exactly one post-idle checkpoint per backend."
    }
    [long]$liteIdlePrivate = $liteIdleCheckpoints[0].aggregatePrivateBytes
    [long]$fastMcpIdlePrivate = $fastMcpIdleCheckpoints[0].aggregatePrivateBytes
    if ($liteIdlePrivate -le 0 -or $fastMcpIdlePrivate -le 0) {
        throw "Validated lite and FastMCP aggregate idle private bytes must both be positive."
    }
    $idleRatio = [double]$liteIdlePrivate / [double]$fastMcpIdlePrivate
    if ([double]::IsNaN($idleRatio) -or [double]::IsInfinity($idleRatio)) {
        throw "Lite/FastMCP idle private ratio was not finite."
    }
    $idleGate = [pscustomobject]@{
        formula = "lite idle aggregate private bytes <= 50% of FastMCP idle aggregate private bytes"
        liteIdlePrivateBytes = $liteIdlePrivate
        fastMcpIdlePrivateBytes = $fastMcpIdlePrivate
        ratio = $idleRatio
        maximumRatio = 0.5
        pass = $idleRatio -le 0.5
    }
    $processAudit = Invoke-OwnedPidAudit -OwnedRegistry $OwnedProcessRecords
    $allAuditEntries = @($probeLifecycle.ownedEntries) + @($processAudit.entries)
    $independentPidRecheck = Invoke-IndependentPidRecheck -AuditEntries @($allAuditEntries)
    $independentPidRecheckComplete = Test-IndependentPidRecheckComplete -Recheck $independentPidRecheck
    $allSessionEvidence = @($lite.processes) + @($fastMcp.processes)
    $exitCodeCompleteness = $allSessionEvidence.Count -eq (2 * $Sessions) -and
        @($allSessionEvidence | Where-Object {
                -not (Test-SessionExitCodesComplete -SessionEvidence $_)
            }).Count -eq 0
    $overallComponents = [pscustomobject]@{
        liteProtocolAccountingCleanup = Test-BackendProtocolAccountingCleanup -BackendRun $lite `
            -ExpectedSessions $Sessions -ExpectedCycles $Cycles
        fastMcpProtocolAccountingCleanup = Test-BackendProtocolAccountingCleanup -BackendRun $fastMcp `
            -ExpectedSessions $Sessions -ExpectedCycles $Cycles
        postRunPidAudit = Test-ProcessAuditComplete -Audit $processAudit
        probeLifecycleCleanup = Test-ProbeLifecycleComplete -Lifecycle $probeLifecycle
        independentPidRecheck = $independentPidRecheckComplete
        exitCodeCompleteness = $exitCodeCompleteness
        memoryEvidenceValidation = $memoryValidation.pass -eq $true
        memoryStability = $liteStability.pass -eq $true -and $fastMcpStability.pass -eq $true
        liteFastMcpIdleRatio = $idleGate.pass -eq $true
        evidenceIntegrity = $integrity.pass -eq $true
    }
    $overallPass = Test-AllExplicitPassComponents -Components $overallComponents
    $timestamp = [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss'Z'")
    $jsonPath = Join-Path $outputPath "task-7-memory-$timestamp.json"
    $markdownPath = Join-Path $outputPath "task-7-memory-$timestamp.md"
    $pidRecheckPath = Join-Path $outputPath "task-7-pid-recheck-$timestamp.json"

    $evidence = [pscustomobject]@{
        schemaVersion = 6
        generatedAtUtc = [DateTime]::UtcNow.ToString("o")
        durationSeconds = [Math]::Round(([DateTime]::UtcNow - $startedAtUtc).TotalSeconds, 3)
        integrity = $integrity
        environment = [pscustomobject]@{
            pythonVersion = $pythonVersion
            pythonExecutable = $pythonExecutable
            fastMcpVersion = $fastMcpVersion
            sdkDllName = [IO.Path]::GetFileName($sdkPath)
            powershellVersion = $PSVersionTable.PSVersion.ToString()
            pythonUsesLauncherChild = $expectRuntimeChild
        }
        config = [pscustomobject]@{
            repoRoot = $repoRootPath
            sessions = $Sessions
            cycles = $Cycles
            idleSeconds = $IdleSeconds
            query = $Query
            scope = $scopePath
            limit = $Limit
            sort = $Sort
            metadata = [bool]$Metadata
            responseDeadlineMode = "absolute"
            responseTimeoutSeconds = $ResponseTimeoutSeconds
            exitTimeoutSeconds = $ExitTimeoutSeconds
            cimOperationTimeoutMaximumSeconds = 2
            runtimeDiscoveryDeadlineSeconds = 5
            cleanupDiscoveryDeadlineSeconds = 3
            probeTimeoutSeconds = 30
            probeCleanupTimeoutSeconds = 5
        }
        backends = @($lite, $fastMcp)
        probeLifecycle = $probeLifecycle
        processAudit = $processAudit
        independentPidRecheck = $independentPidRecheck
        pidRecheckPath = $pidRecheckPath
        memoryValidation = $memoryValidation
        gates = [pscustomobject]@{
            liteIdleVsFastMcp = $idleGate
            stability = @($liteStability, $fastMcpStability)
        }
        overallComponents = $overallComponents
        overallPass = $overallPass
    }

    $pidRecheckEvidence = [pscustomobject]@{
        schemaVersion = 1
        generatedAtUtc = [DateTime]::UtcNow.ToString("o")
        sourceEvidenceFile = [IO.Path]::GetFileName($jsonPath)
        scriptSha256 = $integrity.scriptSha256
        gitHead = $integrity.gitHead
        gitTree = $integrity.gitTree
        pass = $independentPidRecheckComplete
        checkedPids = [int[]]@($independentPidRecheck.checkedPids)
        checkedCount = [int]$independentPidRecheck.checkedCount
        aliveOwnedPids = [int[]]@($independentPidRecheck.aliveOwnedPids)
        aliveOwnedCount = [int]$independentPidRecheck.aliveOwnedCount
        reusedPids = [int[]]@($independentPidRecheck.reusedPids)
        reusedCount = [int]$independentPidRecheck.reusedCount
        unreadablePids = [int[]]@($independentPidRecheck.unreadablePids)
        unreadableCount = [int]$independentPidRecheck.unreadableCount
        entries = [object[]]@($independentPidRecheck.entries)
    }
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($jsonPath, ($evidence | ConvertTo-Json -Depth 100), $utf8WithoutBom)
    [IO.File]::WriteAllText($markdownPath, (New-MarkdownSummary -Evidence $evidence), $utf8WithoutBom)
    [IO.File]::WriteAllText($pidRecheckPath, ($pidRecheckEvidence | ConvertTo-Json -Depth 100), $utf8WithoutBom)

    Write-Host "JSON evidence: $jsonPath"
    Write-Host "Markdown summary: $markdownPath"
    Write-Host "Independent PID recheck: $pidRecheckPath"
    Write-Host ("Lite/FastMCP idle private ratio: {0:F6}" -f $idleRatio)
    if (-not $overallPass) {
        throw "One or more protocol, cleanup, PID audit/recheck, integrity, or memory gates failed. Evidence was written to $jsonPath, $markdownPath, and $pidRecheckPath."
    }
}
finally {
    Stop-AllRegisteredChildren -ProcessRegistry $ActiveSessions
}
