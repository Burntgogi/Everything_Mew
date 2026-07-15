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

function Test-ObjectProperty {
    param(
        [Parameter(Mandatory = $true)]
        [object]$InputObject,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    return $null -ne $InputObject.PSObject.Properties[$Name]
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

function Invoke-PythonProbe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string]$Code,
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

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
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(30000)) {
        $process.Kill()
        $null = $process.WaitForExit(5000)
        throw "$FailureMessage The interpreter probe timed out."
    }
    $stdoutTask.Wait()
    $stderrTask.Wait()
    $stdout = $stdoutTask.Result.Trim()
    $stderr = $stderrTask.Result.Trim()
    $exitCode = $process.ExitCode
    $process.Dispose()

    if ($exitCode -ne 0) {
        $detail = if ($stderr) { $stderr } else { "no diagnostic output" }
        throw "$FailureMessage Interpreter: $Executable. Details: $detail"
    }
    return $stdout
}

function Get-PythonRuntimeChild {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Launcher,
        [Parameter(Mandatory = $true)]
        [string]$Backend,
        [Parameter(Mandatory = $true)]
        [int]$Index
    )

    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ([DateTime]::UtcNow -lt $deadline) {
        $children = @(
            Get-CimInstance Win32_Process -Filter "ParentProcessId = $($Launcher.Id)" |
                Where-Object { $_.Name -ieq "python.exe" -or $_.Name -ieq "pythonw.exe" }
        )
        if ($children.Count -eq 1) {
            return Get-Process -Id $children[0].ProcessId
        }
        if ($children.Count -gt 1) {
            throw "$Backend session $Index launcher created multiple Python runtime children."
        }
        $Launcher.Refresh()
        if ($Launcher.HasExited) {
            throw "$Backend session $Index launcher exited before its Python runtime child was found."
        }
        Start-Sleep -Milliseconds 25
    }
    throw "$Backend session $Index did not create the expected Python runtime child within 5 seconds."
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
        [System.Collections.ArrayList]$ProcessRegistry
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
    $originalConsoleInputEncoding = [Console]::InputEncoding
    try {
        [Console]::InputEncoding = $utf8
        $started = $process.Start()
    }
    finally {
        [Console]::InputEncoding = $originalConsoleInputEncoding
    }
    if (-not $started) {
        throw "Failed to start $Backend session $Index."
    }
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $session = [pscustomobject]@{
        Backend = $Backend
        Index = $Index
        Process = $process
        RuntimeProcess = $process
        StderrTask = $stderrTask
        ExpectedIds = New-Object System.Collections.ArrayList
        SeenIds = @{}
        Notifications = New-Object System.Collections.ArrayList
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
        HungAtEof = $false
        ForcedCleanup = $false
        Stderr = ""
        StderrClean = $false
        RuntimeExitCode = $null
    }
    $null = $ProcessRegistry.Add($session)
    if ($ExpectRuntimeChild) {
        $session.RuntimeProcess = Get-PythonRuntimeChild -Launcher $process -Backend $Backend -Index $Index
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

function Receive-MatchingResponse {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Session,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedId,
        [Parameter(Mandatory = $true)]
        [int]$TimeoutSeconds
    )

    while ($true) {
        $readTask = $Session.Process.StandardOutput.ReadLineAsync()
        if (-not $readTask.Wait($TimeoutSeconds * 1000)) {
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
            if ($hasId) {
                throw "$($Session.Backend) session $($Session.Index) emitted an unsupported server request with an id."
            }
            if ([string]::IsNullOrWhiteSpace([string]$message.method)) {
                throw "$($Session.Backend) session $($Session.Index) emitted a notification without a valid method."
            }
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
    param([object]$Session)

    $issues = New-Object System.Collections.ArrayList
    $trailingOutput = $Session.Process.StandardOutput.ReadToEnd()
    foreach ($line in @($trailingOutput -split "`r?`n" | Where-Object { $_ })) {
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
        if ($hasMethod -and -not $hasId -and -not [string]::IsNullOrWhiteSpace([string]$message.method)) {
            $null = $Session.Notifications.Add([string]$message.method)
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
    return @($issues)
}

function Complete-SessionProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$SessionObjects,
        [Parameter(Mandatory = $true)]
        [int]$TimeoutSeconds
    )

    foreach ($session in $SessionObjects) {
        if (-not $session.InputClosed) {
            try {
                $session.Process.StandardInput.Close()
            }
            finally {
                $session.InputClosed = $true
            }
        }
    }

    $issues = New-Object System.Collections.ArrayList
    foreach ($session in $SessionObjects) {
        if (-not $session.Process.WaitForExit($TimeoutSeconds * 1000)) {
            $session.HungAtEof = $true
            $null = $issues.Add("$($session.Backend) session $($session.Index) hung after stdin EOF")
            $session.Process.Kill()
            $null = $session.Process.WaitForExit(5000)
        }
        $session.ExitCode = $session.Process.ExitCode
        if ($session.RuntimeProcess.Id -ne $session.Process.Id) {
            if (-not $session.RuntimeProcess.HasExited -and -not $session.RuntimeProcess.WaitForExit(1000)) {
                $session.HungAtEof = $true
                $null = $issues.Add("$($session.Backend) session $($session.Index) runtime survived launcher EOF exit")
                $session.RuntimeProcess.Kill()
                $null = $session.RuntimeProcess.WaitForExit(5000)
            }
            if ($session.RuntimeProcess.HasExited) {
                $session.RuntimeExitCode = $session.RuntimeProcess.ExitCode
            }
        }
        else {
            $session.RuntimeExitCode = $session.ExitCode
        }
        $session.StderrTask.Wait()
        $session.Stderr = $session.StderrTask.Result
        $session.StderrClean = $session.Stderr -notmatch "(?im)traceback|unhandled exception|\berror\b"
        if ($session.ExitCode -ne 0) {
            $null = $issues.Add("$($session.Backend) session $($session.Index) exited with code $($session.ExitCode)")
        }
        if (-not $session.StderrClean) {
            $null = $issues.Add("$($session.Backend) session $($session.Index) emitted an error or traceback on stderr")
        }
        foreach ($protocolIssue in @(Get-TrailingProtocolIssues -Session $session)) {
            $null = $issues.Add($protocolIssue)
        }
    }
    return [pscustomobject]@{
        pass = $issues.Count -eq 0
        allExited = @(
            $SessionObjects | Where-Object { -not $_.Process.HasExited -or -not $_.RuntimeProcess.HasExited }
        ).Count -eq 0
        hungCount = @($SessionObjects | Where-Object { $_.HungAtEof }).Count
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
            if (-not $session.Process.WaitForExit(2000)) {
                $session.ForcedCleanup = $true
                $session.Process.Kill()
                $null = $session.Process.WaitForExit(5000)
            }
        }
        catch {
            # Cleanup is restricted to the exact child Process objects created by this script.
        }
        try {
            if ($session.RuntimeProcess.Id -ne $session.Process.Id -and -not $session.RuntimeProcess.HasExited) {
                $session.ForcedCleanup = $true
                $session.RuntimeProcess.Kill()
                $null = $session.RuntimeProcess.WaitForExit(5000)
            }
        }
        catch {
            # The exact runtime child may already have exited with its launcher.
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
    foreach ($session in $SessionObjects) {
        $expectedResponseCount = 1 + (4 * $ExpectedCycles)
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
}

function Convert-SessionEvidence {
    param([object]$Session)

    $stderrLines = @($Session.Stderr -split "`r?`n" | Where-Object { $_ })
    return [pscustomobject]@{
        session = $Session.Index
        pid = $Session.RuntimeProcess.Id
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
        unsolicitedNotifications = @($Session.Notifications)
        sent = [pscustomobject]$Session.SentCounts
        sampleSearchPaths = @($Session.SearchPaths | Select-Object -Unique -First 5)
        exitCode = $Session.ExitCode
        runtimeExitCode = $Session.RuntimeExitCode
        launcherExited = $Session.Process.HasExited
        runtimeExited = $Session.RuntimeProcess.HasExited
        hungAtEof = $Session.HungAtEof
        forcedCleanup = $Session.ForcedCleanup
        stderrClean = $Session.StderrClean
        stderrLineCount = $stderrLines.Count
        stderr = $Session.Stderr.Trim()
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
        [System.Collections.ArrayList]$ProcessRegistry
    )

    Write-Host "Starting $ProcessCount $Backend processes ($Module)..."
    $sessionObjects = @()
    $completedNormally = $false
    try {
        for ($index = 1; $index -le $ProcessCount; $index++) {
            $sessionObjects += Start-McpSession -Backend $Backend -Module $Module -Index $index -Executable $Executable `
                -WorkingDirectory $WorkingDirectory -SdkPath $SdkPath -ExpectRuntimeChild $ExpectRuntimeChild `
                -ProcessRegistry $ProcessRegistry
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

        Assert-SessionAccounting -SessionObjects $sessionObjects -ExpectedCycles $CycleCount
        $cleanup = Complete-SessionProcesses -SessionObjects $sessionObjects -TimeoutSeconds $ExitTimeout
        $completedNormally = $true
        if (-not $cleanup.pass) {
            throw "$Backend cleanup failed: $($cleanup.issues -join '; ')"
        }

        return [pscustomobject]@{
            backend = $Backend
            module = $Module
            processCount = $ProcessCount
            cyclesPerProcess = $CycleCount
            workflowsCompleted = ($sessionObjects | Measure-Object -Property CyclesCompleted -Sum).Sum
            realSearchSuccesses = ($sessionObjects | Measure-Object -Property SearchSuccesses -Sum).Sum
            idsIsolated = $true
            exactResponseIds = $true
            protocolVersion = "2025-06-18"
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

function Get-StabilityGate {
    param(
        [Parameter(Mandatory = $true)]
        [object]$BackendRun,
        [Parameter(Mandatory = $true)]
        [int]$ProcessCount
    )

    $first = $BackendRun.memory.afterFirstSearch
    $idle = $BackendRun.memory.postIdle
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

    [long]$aggregatePlateau = $first.aggregate.privateBytes
    [long]$aggregateIdle = $idle.aggregate.privateBytes
    [long]$aggregateTwentyPercent = [Math]::Ceiling($aggregatePlateau * 0.20)
    [long]$aggregateFloor = $ProcessCount * $PerProcessFloorBytes
    [long]$aggregateAllowance = [Math]::Max($aggregateTwentyPercent, $aggregateFloor)
    [long]$aggregateMaximum = $aggregatePlateau + $aggregateAllowance
    $aggregatePass = $aggregateIdle -le $aggregateMaximum

    return [pscustomobject]@{
        backend = $BackendRun.backend
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
    $null = $lines.Add("")
    $null = $lines.Add("## Protocol and cleanup")
    $null = $lines.Add("")
    $null = $lines.Add("| Backend | Workflows | Real searches | IDs isolated | EOF exits | Hangs |")
    $null = $lines.Add("| --- | ---: | ---: | --- | --- | ---: |")
    foreach ($run in @($Evidence.backends)) {
        $null = $lines.Add("| $($run.backend) | $($run.workflowsCompleted) | $($run.realSearchSuccesses) | $($run.idsIsolated) | $($run.cleanup.allExited) | $($run.cleanup.hungCount) |")
    }
    $null = $lines.Add("")
    $null = $lines.Add("Every status response was validated as installed/running with backend ``sdk-ipc`` and a loaded database. Every count was numeric and positive; every search returned an existing file inside the configured scope.")
    $null = $lines.Add("")
    $null = $lines.Add("## Memory")
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

$probe = Invoke-PythonProbe -Executable $pythonExecutable `
    -Code "import fastmcp, os, platform, sys; print(platform.python_version() + '|' + str(getattr(fastmcp, '__version__', 'unknown')) + '|' + ('1' if os.path.normcase(sys.executable) != os.path.normcase(getattr(sys, '_base_executable', sys.executable)) else '0'))" `
    -FailureMessage "FastMCP is required for the comparison. Install the project server extra into the selected interpreter."
$probeParts = $probe -split '\|', 3
$pythonVersion = $probeParts[0]
$fastMcpVersion = if ($probeParts.Count -gt 1) { $probeParts[1] } else { "unknown" }
$expectRuntimeChild = $probeParts.Count -gt 2 -and $probeParts[2] -eq "1"

$startedAtUtc = [DateTime]::UtcNow
try {
    $lite = Invoke-BackendMeasurement -Backend "lite" -Module "everything_mcp.lite_stdio" -Executable $pythonExecutable `
        -WorkingDirectory $repoRootPath -SdkPath $sdkPath -ScopePath $scopePath -SearchQuery $Query `
        -ProcessCount $Sessions -CycleCount $Cycles -IdleDurationSeconds $IdleSeconds -SearchLimit $Limit `
        -SearchSort $Sort -SearchMetadata ([bool]$Metadata) -ResponseTimeout $ResponseTimeoutSeconds `
        -ExitTimeout $ExitTimeoutSeconds -ExpectRuntimeChild $expectRuntimeChild -ProcessRegistry $ActiveSessions

    $fastMcp = Invoke-BackendMeasurement -Backend "fastmcp" -Module "everything_mcp" -Executable $pythonExecutable `
        -WorkingDirectory $repoRootPath -SdkPath $sdkPath -ScopePath $scopePath -SearchQuery $Query `
        -ProcessCount $Sessions -CycleCount $Cycles -IdleDurationSeconds $IdleSeconds -SearchLimit $Limit `
        -SearchSort $Sort -SearchMetadata ([bool]$Metadata) -ResponseTimeout $ResponseTimeoutSeconds `
        -ExitTimeout $ExitTimeoutSeconds -ExpectRuntimeChild $expectRuntimeChild -ProcessRegistry $ActiveSessions

    $liteStability = Get-StabilityGate -BackendRun $lite -ProcessCount $Sessions
    $fastMcpStability = Get-StabilityGate -BackendRun $fastMcp -ProcessCount $Sessions
    [long]$liteIdlePrivate = $lite.memory.postIdle.aggregate.privateBytes
    [long]$fastMcpIdlePrivate = $fastMcp.memory.postIdle.aggregate.privateBytes
    if ($fastMcpIdlePrivate -le 0) {
        throw "FastMCP aggregate idle private bytes were not positive."
    }
    $idleRatio = [double]$liteIdlePrivate / [double]$fastMcpIdlePrivate
    $idleGate = [pscustomobject]@{
        formula = "lite idle aggregate private bytes <= 50% of FastMCP idle aggregate private bytes"
        liteIdlePrivateBytes = $liteIdlePrivate
        fastMcpIdlePrivateBytes = $fastMcpIdlePrivate
        ratio = $idleRatio
        maximumRatio = 0.5
        pass = $idleRatio -le 0.5
    }
    $overallPass = $idleGate.pass -and $liteStability.pass -and $fastMcpStability.pass

    $evidence = [pscustomobject]@{
        schemaVersion = 1
        generatedAtUtc = [DateTime]::UtcNow.ToString("o")
        durationSeconds = [Math]::Round(([DateTime]::UtcNow - $startedAtUtc).TotalSeconds, 3)
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
            responseTimeoutSeconds = $ResponseTimeoutSeconds
            exitTimeoutSeconds = $ExitTimeoutSeconds
        }
        backends = @($lite, $fastMcp)
        gates = [pscustomobject]@{
            liteIdleVsFastMcp = $idleGate
            stability = @($liteStability, $fastMcpStability)
        }
        overallPass = $overallPass
    }

    $timestamp = [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss'Z'")
    $jsonPath = Join-Path $outputPath "task-7-memory-$timestamp.json"
    $markdownPath = Join-Path $outputPath "task-7-memory-$timestamp.md"
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($jsonPath, ($evidence | ConvertTo-Json -Depth 100), $utf8WithoutBom)
    [IO.File]::WriteAllText($markdownPath, (New-MarkdownSummary -Evidence $evidence), $utf8WithoutBom)

    Write-Host "JSON evidence: $jsonPath"
    Write-Host "Markdown summary: $markdownPath"
    Write-Host ("Lite/FastMCP idle private ratio: {0:F6}" -f $idleRatio)
    if (-not $overallPass) {
        throw "One or more memory gates failed. Evidence was written to $jsonPath and $markdownPath."
    }
}
finally {
    Stop-AllRegisteredChildren -ProcessRegistry $ActiveSessions
}
