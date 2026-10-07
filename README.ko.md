<p align="center">
  <img src="docs/assets/everything-mew-banner.png" width="420" alt="AI 에이전트의 파일 경로 탐색을 돕는 Everything_Mew 고양이 마스코트">
</p>

<h1 align="center">Everything_Mew</h1>

<p align="center"><strong>Windows에서 AI 에이전트가 인덱스로 파일을 찾게 합니다. 디렉터리를 훑으면 10~30초 걸리는 검색을 약 0.1초에 끝냅니다.</strong></p>

<p align="center">
  <a href="https://github.com/Burntgogi/Everything_Mew/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Burntgogi/Everything_Mew/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/Burntgogi/Everything_Mew/releases/tag/v0.4.0"><img alt="안정판 v0.4.0" src="https://img.shields.io/badge/stable-v0.4.0-5865F2"></a>
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows-0078D4">
  <img alt="Python 3.11부터 3.14" src="https://img.shields.io/badge/Python-3.11--3.14-3776AB">
  <img alt="읽기 전용 MCP 도구" src="https://img.shields.io/badge/MCP-read--only-1F883D">
  <a href="LICENSE"><img alt="Apache-2.0 라이선스" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="#everything-인덱스를-쓰는-이유">벤치마크</a> ·
  <a href="#빠른-시작">빠른 시작</a> ·
  <a href="#도구">도구</a> ·
  <a href="#구조">구조</a> ·
  <a href="#안전-경계">안전 경계</a> ·
  <a href="#릴리스-이력">릴리스 노트</a>
</p>

## 개요

Everything_Mew는 AI 에이전트를 로컬
[Everything](https://www.voidtools.com/) 인덱스에 연결합니다. Everything은
색인한 볼륨의 모든 파일·폴더 이름을 이미 메모리에 갖고 있습니다.
Everything_Mew를 쓰면 에이전트가 이 인덱스에 질의해 이름, 경로, 확장자, 크기,
날짜로 파일을 찾고, 고른 파일만 평소 도구로 읽습니다.

코딩 에이전트는 보통 디렉터리를 직접 훑어서 파일을 찾습니다. Claude Code의
Glob은 ripgrep으로 트리를 순회하고, 셸에서는 `Get-ChildItem -Recurse`를
씁니다. 순회는 호출할 때마다 모든 디렉터리를 읽으므로 트리가 클수록
느려집니다. 인덱스 질의는 트리를 순회하지 않으므로 약 0.1초에 끝납니다.

이 프로젝트는 Windows 전용이며 읽기 전용 MCP 도구 네 개를 제공합니다. 파일을
수정하거나 Everything 인덱스를 변경하지 않으며 Everything HTTP 서버도
활성화하지 않습니다. 저장소에는 간결한 Everything 문법을 안내하는 선택적
에이전트 스킬도 포함되어 있습니다. Python wheel은 MCP 런타임과 콘솔
진입점만 설치합니다. 런타임은 외부 의존성이 없고 Everything SDK DLL도 필요하지
않습니다.

## Everything 인덱스를 쓰는 이유

Windows 11 컴퓨터 한 대(Everything 1.4.1.1024, 색인 파일 약 880만 개)에서
Claude Code의 검색 방법을 측정했습니다. Glob은 Claude Code에 내장된 ripgrep을
Glob 도구와 같은 인수로 실행한 것입니다. PowerShell은 재귀
`Get-ChildItem`입니다. Everything_Mew는 실행 중인 `everything-mew-lite` 서버에
보낸 MCP 도구 호출 한 번입니다. 모든 값은 반복 측정의 중앙값입니다.

| 검색 | Glob | PowerShell | Everything_Mew |
| --- | ---: | ---: | ---: |
| `*.toml`, 사용자 프로필(60만 3천 파일) | 9.0초 | 25.7초 | **0.15초** |
| `pyproject.toml`, 사용자 프로필(60만 3천 파일) | 10.2초 | 25.8초 | **0.11초** |
| 이름에 `lite` 포함, 사용자 프로필(60만 3천 파일) | 9.9초 | 28.8초 | **0.18초** |
| 오늘 수정된 `.py`, 사용자 프로필(60만 3천 파일) | 지원 안 함 | 29.3초 | **0.18초** |
| `*.toml`, 저장소 하나(3만 6천 파일) | 0.35초 | 1.2초 | **0.11초** |

비교 가능한 검색에서는 결과도 같았습니다. 세 방법 모두 `.toml` 553개와
`pyproject.toml` 30개를 똑같이 찾았습니다.

같은 과제를 `claude -p`(Sonnet, 과제당 2회)로 처음부터 끝까지도 실행했습니다.

| 과제 | 기본 도구만 사용 | Everything_Mew 사용 |
| --- | --- | --- |
| 사용자 프로필의 모든 `pyproject.toml` 찾기 | 27.2초, 3턴, 1회 개수 오답 | **18.8초, 2턴, 2회 모두 정답** |
| 사용자 프로필에서 오늘 수정된 `.py` 찾기 | 216.7초, 1회 오답 | **16.5초, 2턴, 2회 모두 정답** |
| 저장소 하나의 `.toml` 목록 | **7.3초** | 11.2초 |

Windows 에이전트 작업에서의 의미는 다음과 같습니다.

- **크거나 위치를 모르는 범위에서 이득이 가장 큽니다.** 사용자 프로필, 여러
  프로젝트, "이 드라이브 어딘가"를 훑으려면 몇 초에서 몇 분이 걸립니다.
  Everything은 1초도 안 걸리므로 에이전트가 파일을 찾느라 기다리지 않습니다.
- **날짜·크기 조건이 호출 한 번으로 끝납니다.** Glob은 날짜나 크기로 거르지
  못합니다. 인덱스가 없으면 에이전트가 느린 셸 파이프라인을 작성하는데, 측정
  중 이 방식이 오답을 낸 적이 있습니다.
- **턴이 줄고 비용이 내려갑니다.** `everything_search` 한 번으로 경로와
  `totalCount`를 함께 받으므로 개수를 따로 세는 호출이 필요 없습니다.
- **작고 위치를 아는 폴더에서는 이득이 없습니다.** 저장소 하나 안에서는 Glob도
  이미 빠릅니다. 마지막 행의 차이는 기동 시간입니다. `claude -p`는 MCP 서버에
  연결하는 데 약 3초를 더 쓰지만, Everything_Mew의 핸드셰이크는 0.12초입니다.
  대화형 세션은 이 비용을 검색마다가 아니라 시작할 때 한 번만 냅니다.

측정 방법, 원시 수치, 한계는
[2026-10 감사 문서](docs/AUDIT_2026-10_LIFECYCLE_NATIVE_IPC.md#claude-code-benchmark)에
있습니다. 실제 수치는 디스크, 트리 크기, Everything 색인 설정에 따라
달라집니다.

## 작동 방식

Everything_Mew를 토큰 사용량이 적은 후보 탐색 계층으로 사용한 뒤, 선택한
경로는 일반 콘텐츠 또는 코드 도구로 확인하세요.

```text
everything_search (totalCount 확인) -> read/grep/ast-grep/LSP on selected paths
```

- `everything_search`는 경로 중심의 간결한 후보와 선택적 메타데이터, 그리고
  같은 쿼리에서 얻은 Everything 전체 일치 개수인 `totalCount`를 반환합니다.
  호출 한 번으로 결과 규모와 표본을 함께 얻을 수 있습니다.
- `everything_count`는 경로 목록을 만들지 않고 결과 규모만 확인합니다.
- 파일 내용과 코드 의미는 `read`, `grep`, `ast-grep`, LSP로 확인하세요.

Codex에는 에이전트 스킬과 `everything-mew-once` 사용을 권장합니다. 요청할
때마다 수명이 짧은 프로세스 하나가 실행되어 결과 하나를 반환한 뒤 종료하므로
사용하지 않을 때는 Everything_Mew Python 프로세스가 남지 않습니다. 공유
인덱서인 `Everything.exe`는 계속 실행됩니다.

OpenCode, Claude Code 등 MCP 호스트는 `everything-mew-lite`를 사용합니다.
호스트가 작업 동안 이 stdio 프로세스를 유지하므로, 이 프로세스는 작은 프로토콜
중개자로만 동작합니다. Everything 도구 호출은 매번 새 one-shot 워커
프로세스에서 실행되고 응답 후 종료합니다. 워커 메모리는 호출마다 Windows에
반환되며, 멈춘 호출은 워커를 종료해 중단합니다.
[실행 수명 주기](#실행-수명-주기)를 참고하세요.

## 빠른 시작

### 1. Everything과 Python을 준비하세요

다음 환경이 필요합니다.

- Windows에 표준 Everything 애플리케이션을 설치하고 실행해야 합니다.
- CPython 3.11부터 3.14까지 지원합니다.

Everything Lite는 IPC 인터페이스를 제공하지 않으므로 지원하지 않습니다.
Everything SDK DLL은 필요하지 않습니다. Everything_Mew가 공개된 Everything IPC
프로토콜을 직접 사용합니다.

### 2. 0.4.0 이상을 설치하세요

허용 경로 정책을 실제로 적용하는 첫 릴리스는 0.4.0입니다. 0.3.0은 아래 정책
설정을 무시합니다. `v0.4.0` 태그를 설치하세요.

```powershell
git clone https://github.com/Burntgogi/Everything_Mew.git
cd Everything_Mew
git checkout --detach v0.4.0
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -c "from everything_mcp.policy import SearchPolicy; assert SearchPolicy().denial_reason(None) is not None; print('Allowed-root policy available')"
```

정책 확인에 실패하면 멈추세요. 확인이 통과하기 전에는 검색 호스트를
활성화하지 마세요.

### 3. Claude Code에 lite 서버를 등록하세요

서버가 검색할 수 있는 디렉터리를 사용자 환경 변수로 지정합니다. 값은 절대
경로의 JSON 배열입니다. Claude Code는 자신의 환경 변수를 서버에 넘깁니다.

```powershell
[Environment]::SetEnvironmentVariable('EVERYTHING_MCP_ALLOWED_ROOTS', '["C:\\Users\\me","D:\\Projects"]', 'User')
$lite = (Resolve-Path ".\.venv\Scripts\everything-mew-lite.exe").Path
claude mcp add everything-mew -s user -- $lite
```

Windows PowerShell 5.1에서 `claude mcp add -e`로 JSON을 넘기지 마세요. 이
셸은 네이티브 명령 인수의 큰따옴표를 지우므로 서버가 그 값을 거부합니다.

새 터미널을 열고 Claude Code를 시작한 뒤 파일 검색을 요청하세요.
`everything_search` 도구가 세션과 함께 로드되므로 에이전트는 도구 검색 턴 없이
바로 사용합니다.

### 4. Codex에서 one-shot 실행기를 사용하세요

설치 명령을 Codex 프로세스에서 찾을 수 있게 하세요. 호출할 때마다 절대 경로로
확인하세요.

```powershell
$env:EVERYTHING_MCP_ALLOWED_ROOTS = '["C:\\Work\\project"]'
$runner = (Resolve-Path ".\.venv\Scripts\everything-mew-once.exe").Path
$request = @{
    schemaVersion = 1
    tool = "everything_status"
    arguments = @{}
} | ConvertTo-Json -Compress -Depth 4
$resultJson = $request | & $runner
$exitCode = $LASTEXITCODE
if ($exitCode -notin 0, 1) { throw "Everything_Mew one-shot invocation failed." }
$resultJson | ConvertFrom-Json
```

기존 Codex MCP 등록을 롤백 경로로 남기려면 비활성 상태로 유지하세요.

```toml
[mcp_servers.everything-mew]
command = "everything-mew-lite"
args = []
enabled = false
startup_timeout_sec = 20
tool_timeout_sec = 20
enabled_tools = ["everything_status", "everything_count", "everything_search", "everything_syntax_help"]

[mcp_servers.everything-mew.env]
EVERYTHING_MCP_ALLOWED_ROOTS = '["C:\\Work\\project"]'
```

`enabled = false`는 MCP를 사용할 수 없게 하는 설정이며, 필요할 때 자동으로
깨우는 수면 모드가 아닙니다. 이 설정을 바꾼 뒤에는 Codex를 완전히
재시작하세요. 이미 실행 중인 Codex 또는 OpenCode 호스트가 소유한 서버는 해당
호스트가 종료될 때만 함께 종료됩니다. 검색하기 전에 one-shot
`everything_status` 결과에서 백엔드가 `native-ipc`인지, 데이터베이스가
로드되었는지 확인하세요.

OpenCode에서는 부모 프로세스의 환경 변수에 허용 경로를 설정하고 다음과 같이
환경 변수 자리표시자를 그대로 사용하세요.

```json
{
  "mcp": {
    "everything-mew": {
      "type": "local",
      "command": ["everything-mew-lite"],
      "enabled": true,
      "environment": {
        "EVERYTHING_MCP_ALLOWED_ROOTS": "{env:EVERYTHING_MCP_ALLOWED_ROOTS}"
      }
    }
  }
}
```

네이티브 IPC 대신 Everything SDK DLL을 쓰려면 `EVERYTHING_SDK_DLL`을 Python
아키텍처와 일치하는 신뢰할 수 있는 DLL로 지정하고
`EVERYTHING_MCP_BACKEND=sdk`를 설정하세요.

전체 경로 검증, 설치, 스모크 테스트 절차는
[에이전트 설치 가이드](docs/AGENT_INSTALLATION_GUIDE.md)와
[SDK 설치 가이드](docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md)를 참조하세요.

### 선택적 FastMCP 호환 경로

호스트에서 기존 FastMCP 진입점이 명시적으로 필요한 경우에만 호환 런타임을
설치하세요.

```powershell
.\.venv\Scripts\python.exe -m pip install ".[server]"
```

소스 체크아웃을 editable 방식으로 사용하는 경우에는 다음 호환 명령을
실행하세요.

```powershell
py -m pip install -e ".[server]"
```

기존 `everything-mew`와 `everything-mcp` 명령은 이 선택적 경로를 사용합니다.
Codex 권장 명령인 `everything-mew-once`, 별칭 `everything-mcp-once`, lite
호환 명령에는 FastMCP가 필요하지 않습니다.

## 도구

| 도구 | 용도 |
| --- | --- |
| `everything_status` | Everything, 데이터베이스, 아키텍처, 선택된 백엔드의 준비 상태를 보고합니다. |
| `everything_count` | 경로를 반환하지 않고 범위가 제한된 쿼리의 결과 수만 확인합니다. |
| `everything_search` | 경로 중심의 간결한 후보, `totalCount`, 선택적 메타데이터를 반환합니다. |
| `everything_syntax_help` | Everything 버전을 고려한 간결한 쿼리 문법을 안내합니다. |

도구 네 개는 모두 읽기 전용입니다. 공개 MCP 계약은 후보 경로를 찾는 단계까지만
담당합니다.

## 요구 사항

- **운영체제:** Windows가 필요합니다.
- **Everything:** 표준 애플리케이션을 설치하고 실행해야 합니다.
- **Everything SDK:** 선택 사항입니다. 기본 네이티브 IPC 백엔드에는 DLL이
  필요하지 않습니다. `EVERYTHING_SDK_DLL`을 설정한 경우에만 Python
  아키텍처와 일치하는 신뢰할 수 있는 DLL을 사용합니다.
- **Python:** CPython 3.11, 3.12, 3.13, 3.14를 지원합니다.
- **MCP 호스트:** Codex Desktop 또는 로컬 stdio MCP를 지원하는 호스트가
  필요합니다.
- **네트워크 서비스:** Everything HTTP는 필요하지 않으며 자동으로 활성화하지
  않습니다.

공식 문서는 다음 링크에서 확인하세요.

- [Everything](https://www.voidtools.com/)
- [Everything 1.4 검색 가이드](https://www.voidtools.com/support/everything/searching/)
- [Everything 1.5 검색 문법](https://www.voidtools.com/support/everything/search_syntax/)
- [Everything SDK](https://www.voidtools.com/support/everything/sdk/)
- [Everything_SetMax](https://www.voidtools.com/support/everything/sdk/everything_setmax/)
- [Everything_GetTotResults](https://www.voidtools.com/support/everything/sdk/everything_gettotresults/)

## 검색 문법 호환성과 안전

`everything_count`와 `everything_search`를 사용하기 전에
루트와 `scope`에는 `C:\Work\project` 같은 일반 드라이브 경로나
`\\server\share\project` 같은 UNC 경로를 사용하세요. 장치·확장 경로 접두사
`\\.\`와 `\\?\`는 지원하지 않습니다.

`EVERYTHING_MCP_ALLOWED_ROOTS`에 허용할 절대 Windows 디렉터리를 JSON 배열로
설정하세요. PowerShell과 TOML에서는 예를 들어
`'["C:\\Work\\project"]'`를 사용합니다. 두 도구 호출 모두 허용된 디렉터리
안의 `scope`를 지정해야 합니다. 허용 경로를 설정하지 않으면 Everything
백엔드를 호출하기 전에 검색과 건수 조회를 거부합니다. `metadata=true`를
사용하려면 실행 환경에 `EVERYTHING_MCP_ALLOW_METADATA=1`도 설정해야 합니다.

기존의 전체 인덱스 검색을 명시적으로 허용하려면 허용 경로 대신
`EVERYTHING_MCP_ALLOW_UNSCOPED=1`을 설정할 수 있습니다. 이 모드는
`EVERYTHING_MCP_ALLOWED_ROOTS`와 함께 사용할 수 없습니다. 두 모드 모두 기존의
광범위 쿼리 검사를 유지합니다. 이 설정은 도구 인자가 아니라 호스트 또는
one-shot 프로세스의 환경 변수로 전달하고, 상주 MCP 호스트에서는 변경 후
재시작하세요.

Everything 1.4.1 핵심 문법을 기본 호환 프로필로 사용합니다. Everything 1.5에서만
문서화된 기능을 사용하기 전에는 `everything_status`를 확인하세요. 쿼리는
Python 어댑터가 아니라 실행 중인 Everything 프로세스에서 해석합니다.

범위가 제한된 절대 디렉터리를 `scope` 인자로 전달하세요. 예를 들어
`scope="C:\Work\project"`는 정확한 재귀 폴더 항목
`"C:\Work\project\"`로 조합됩니다. 쿼리 전체를 그 경로 조건으로 묶고
반환 경로가 정규화된 범위 안에 있는지 다시 확인합니다.
`path:"C:\Work\project"` 같은 부분 일치 쿼리는
`C:\Work\project-backup`처럼 접두사가 같은 형제 경로도 포함할 수 있으므로
동일한 표현이 아닙니다.

광범위 쿼리 검사는 인용, 부정, 그룹, 모든 OR 분기를 파싱합니다. 가능한 각
분기는 개별적으로 범위가 제한되어야 합니다. 콘텐츠, 디스크 직접 읽기, 정규식
검색에는 별도의 인덱스 축소 조건이 필요합니다. 전체 일치 와일드카드, 제외
조건, 정규식만으로는 축소 조건을 충족하지 않습니다.

Everything 1.5의 `content*:` 리터럴 꼬리 형식과 `from-disk:`는 느린 I/O
작업으로 취급합니다. 영향을 받는 각 분기에서 루트가 아닌 scope와 별도의
인덱스 필터를 함께 사용하세요.

간결한 안내는 `everything_syntax_help`를 사용하시고 전체 문법은 공식 Everything
문서를 참조하세요.

## 구조

```text
AI agent
  -> one-shot process or MCP compatibility host
  -> Everything_Mew read-only tools
  -> native IPC adapter (SDK DLL or ES CLI as fallbacks)
  -> Everything runtime
  -> Existing Everything index
  -> compact path candidates
  -> read/grep/ast-grep/LSP for selected files
```

기본 어댑터는 공개된 Everything 1.4 IPC 프로토콜(`everything_ipc.h`)을 직접
사용합니다. `QUERY2` 요청 하나를 보내고 `LIST2` 응답 하나를 복사한 뒤 경계
검사를 하며 해석합니다. SDK DLL과 같은 통신 규칙을 따르지만 모든 메시지를
`SendMessageTimeout`으로 보냅니다(SDK는 제한 없는 `SendMessage` 사용). 또한
응답 창에서 UIPI를 통과하는 `WM_COPYDATA`를 허용해 관리자 권한 에이전트도
응답을 받으며, 쿼리마다 고유 응답 ID와 최대 15초 대기를 사용하고 초기화할
전역 SDK 상태를 두지 않습니다. `everything_count`는 `Everything_SetMax(0)`과
같이 결과 플래그를 0, 최대 결과 수를 0으로 두고 전체 개수만 요청합니다.
`everything_search`는 응답의 전체 개수를 `totalCount`로 알려 줍니다.

Everything은 AND 피연산자를 왼쪽부터 평가하고, 신뢰된 범위는 전체 경로
일치입니다. 서버는 다음 두 조건을 모두 만족할 때만 묶은 쿼리를 범위보다 앞에
둡니다.

- 쿼리가 색인된 필터로 시작합니다. `ext:`, `wfn:`, `file:`, `folder:`,
  `name*`이나 `*.py` 같은 고정 와일드카드가 해당합니다. `size:`와 `dm:`은
  Everything이 크기와 수정 날짜를 색인한다고 보고할 때만 해당합니다.
- `content:`나 `from-disk:`처럼 디스크를 읽는 항이 없습니다.

그 밖의 경우에는 범위를 앞에 두어, Everything이 느린 작업 전에 범위부터 좁히게
합니다. 두 순서의 결과는 같습니다. 1.4.1 인덱스에서 범위를 지정한 `ext:py`
검색은 약 64ms에서 27ms로 줄었습니다.

one-shot 경로는 크기가 제한된 JSON 요청 하나를 검증하고 같은 도구
디스패처를 사용해 결과 하나를 반환한 뒤 종료합니다. 데몬, HTTP 리스너,
프로세스 풀을 추가하지 않습니다.

lite MCP 경로는 `initialize`, notification, `ping`, `tools/list`, `tools/call`에
필요한 작은 stdio 수명 주기를 직접 구현하며, Windows 코드 페이지와 관계없이
UTF-8로 읽고 씁니다. FastMCP 서버는 같은 도구 함수와 어댑터를 사용하는 선택적
호환 계층으로 유지합니다.

### 실행 수명 주기

`everything-mew-lite`는 중개자입니다. 인수를 검증하고
`everything_syntax_help`는 정적 텍스트로 직접 답하며, 나머지 도구 호출은
워커에서 실행합니다. 워커는 기본 Python 인터프리터를 격리 모드(`-I -S`, venv
리다이렉터·`.pth` 처리·`PYTHON*` 환경 변수 없음)로 실행해 one-shot 실행기를
구동합니다. 워커가 ctypes와 어댑터를 불러와 Everything에 질의하고 결과 하나를
출력한 뒤 종료하므로 그 메모리는 Windows에 반환됩니다.

| 모드 | 검색 10회 후 상주 중개자 | 호출당 지연 |
| --- | --- | --- |
| 0.3.x 인프로세스 lite | 작업 집합 22.8 MB, 유지됨 | 약 88 ms |
| 워커(기본값) | 작업 집합 18.2 MB | 약 110 ms, 워커 최대 약 17 MB는 종료 시 반환 |
| `EVERYTHING_MCP_EXECUTION=inprocess` | 작업 집합 20.5 MB, 유지됨 | 약 34 ms |

Windows 11, Python 3.13, Everything 1.4.1.1024에서 범위를 지정한 `ext:py`
검색(경로 20개 반환)으로 측정했습니다. 한 컴퓨터의 증거이며 보장값이
아닙니다.

| 변수 | 기본값 | 효과 |
| --- | --- | --- |
| `EVERYTHING_MCP_EXECUTION` | `worker` | `inprocess`는 지연을 줄이기 위해 백엔드를 서버에 유지합니다. |
| `EVERYTHING_MCP_WORKER_TIMEOUT` | `30` | 워커를 종료하고 `isError=true`로 실패시키기까지의 초입니다. |
| `EVERYTHING_MCP_IDLE_EXIT_SECONDS` | 미설정 | 이 시간(초) 동안 요청이 없으면 lite 서버를 종료합니다. stdio 서버를 필요할 때 다시 시작하는 호스트에서만 사용하세요. |
| `EVERYTHING_MCP_BACKEND` | `auto` | `native`, `sdk`, `es` 중 하나로 백엔드를 고정합니다. |
| `EVERYTHING_INSTANCE` | 미설정 | 이름 있는 Everything 인스턴스입니다(예: `1.5a`). |

실패한 호출은 빈 결과가 아니라 도구 오류입니다. 백엔드 사용 불가, 쿼리 실패,
정책 거부, 설정 오류는 `error.code`와 함께 `isError=true`를 반환하고 one-shot
실행기는 `1`로 종료합니다.

## 사용 예시

정확한 파일 이름을 찾으세요. 전체 일치 개수는 `totalCount`에서 확인합니다.

```text
everything_search(query="wfn:pyproject.toml", scope="C:\Work")
```

경로가 필요 없고 개수만 필요할 때는 다음과 같이 호출하세요.

```text
everything_count(query="ext:md", scope="C:\Work\project")
```

범위가 제한된 프로젝트 디렉터리에서 검색하세요.

```text
everything_search(
  query="config ext:json;yml;yaml !node_modules !.git",
  scope="C:\Work\project",
  limit=50
)
```

다음과 같은 Everything 쿼리도 사용할 수 있습니다.

```text
ext:py dm:today
dm:thisweek ext:py;md;json
size:>100mb
lite*
regex:"gr(a|e)y" ext:txt
```

Everything 1.4에는 `name:` 함수가 없습니다. `lite` 같은 일반 단어만으로도 그
단어를 포함한 이름을 찾습니다.

후보를 찾은 뒤에는 선택한 경로만 콘텐츠 인식 도구로 확인하세요.

## 안전 경계

Everything_Mew는 읽기 전용입니다. 다음 작업을 제안하거나 수행하지 않습니다.

- 파일 삭제, 이동, 이름 변경, 격리, 정리를 수행하지 않습니다.
- 중복 파일 제거 워크플로를 수행하지 않습니다.
- Everything 인덱스나 설정을 변경하지 않습니다.
- 범위가 제한되지 않은 전체 드라이브 결과를 덤프하지 않습니다.
- HTTP 서비스를 자동으로 활성화하지 않습니다.
- 인덱싱된 메타데이터 검색으로 위장해 파일 내용을 검사하지 않습니다.

쿼리 보호 장치는 실수로 광범위하거나 느린 검색을 실행할 가능성을 줄이지만 OS
샌드박스는 아닙니다. 위험도가 높은 작업은 권한을 적절히 제한한 Windows 계정
또는 다른 운영체제 격리 환경에서 실행하세요.

다음 신뢰 한계를 알아 두세요.

- **파일 이름은 신뢰할 수 없는 입력입니다.** 파일을 만들 수 있는 사람은 누구나
  이름을 정할 수 있고, 에이전트는 반환된 경로를 텍스트로 읽습니다. 지시문처럼
  보이는 경로는 데이터로만 취급하세요.
- **Everything IPC 창은 인증되지 않습니다.** 같은 Windows 사용자로 실행되는
  프로그램은 Everything 창 클래스를 등록할 수 있습니다. 그러면 쿼리를 읽고 거짓
  결과를 돌려줄 수 있습니다. SDK DLL과 `es.exe`도 마찬가지입니다. 이 경우에도
  허용 경로는 범위 밖의 경로를 모두 걸러 냅니다.
- **응답은 UIPI 경계를 넘습니다.** 에이전트가 관리자 권한이고 Everything이
  아니면, 응답 창은 낮은 무결성 수준의 `WM_COPYDATA`를 받아야 합니다. 네이티브
  IPC 백엔드는 쿼리마다 무작위 32비트 응답 ID를 쓰므로 다른 프로그램이 응답을
  끼워 넣기 어렵습니다.

취약점은 [SECURITY.md](SECURITY.md)의 비공개 절차로 신고하세요. 공개 이슈에는
자격 증명, 비공개 경로, 개인 파일 내용을 첨부하지 마세요.

## 검증

다음 명령으로 로컬 검사를 실행합니다.

```powershell
py -m pytest -q
py -m ruff check .
py -m mypy --strict src tests
py -m build
```

`v0.4.0` 릴리스 후보는 Python 3.13에서 다음 로컬 검증을 통과했습니다.

- pytest 537건, Ruff, 소스·테스트 37개 파일에 대한 strict mypy를
  통과했습니다.
- 실제 Everything 1.4.1 인덱스에서 쿼리·정렬·메타데이터 조합 6가지에 대해
  네이티브 IPC 결과가 Everything SDK DLL과 같았습니다.
- 네이티브 IPC로 ASCII, 한글, 이모지 파일 이름을 실제로 검색했습니다.
- `scripts/measure_lite_sessions.ps1`을 lite 세션 2개와 FastMCP 세션 2개로
  실행했습니다. 소유한 프로세스 12개가 모두 종료됐고 독립 PID 재검사도
  깨끗했습니다.
- Bandit 검사에서는 셸 없이 고정 인수로 호출하는 `subprocess`에 대한 낮은
  심각도 알림만 나왔습니다.

푸시와 pull request에는 Windows GitHub Actions가 Python 3.11과 3.14 검증을
수행합니다. 최종 검증 결과는
[v0.4.0 릴리스 노트](docs/releases/v0.4.0.md)를 참조하세요. 재현
가능한 빌드와 설치 wheel 검증 명령은 [CONTRIBUTING.md](CONTRIBUTING.md)에서
확인하세요.

## 릴리스 이력

| 릴리스 계열 | 역할 | 패키지 버전 | Git 태그 |
| --- | --- | --- | --- |
| 0.1 | 기존 FastMCP 기반 기준판 | `0.1.0` | `v0.1.0` |
| 0.2 | 안정 저대기 부담 MCP 릴리스 | `0.2.0` | `v0.2.0` |
| 0.3 | one-shot 릴리스 | `0.3.0` | `v0.3.0` |
| 0.4 | 현재 안정 네이티브 IPC 릴리스 | `0.4.0` | `v0.4.0` |

0.4는 Everything SDK DLL 요구를 없애고, lite 도구 호출을 메모리를 반환하는
짧은 워커에서 실행하며, 허용 경로 정책을 적용합니다. 공개 도구 네 개의 이름과
인수는 그대로입니다. `everything_search`에 `totalCount`가 추가됐고, 실패는 이제
`isError=true`로 보고합니다.

한영 릴리스 노트는 [v0.1.0](docs/releases/v0.1.0.md),
[v0.2.0](docs/releases/v0.2.0.md), [v0.3.0](docs/releases/v0.3.0.md), 현재
[v0.4.0](docs/releases/v0.4.0.md) 릴리스 문서에서 확인하세요. 전체 한영
이력은 [CHANGELOG.md](CHANGELOG.md)를 참조하세요.

## 저장소 구성

- [`src/everything_mcp/`](src/everything_mcp/)에는 Python MCP 런타임과
  어댑터가 있습니다.
- [`tests/`](tests/)에는 프로토콜, 안전, 어댑터, E2E 중심 테스트가 있습니다.
- [`skills/everything/SKILL.md`](skills/everything/SKILL.md)에는 Everything
  검색을 위한 선택적 에이전트 지침이 있습니다.
- [`docs/AGENT_INSTALLATION_GUIDE.md`](docs/AGENT_INSTALLATION_GUIDE.md)에는
  안전한 호스트 설치 절차가 있습니다.
- [`docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md`](docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md)에는
  SDK 중심 설치와 검증 절차가 있습니다.
- [`docs/releases/`](docs/releases/)에는 한영 릴리스 노트가 있습니다.
- [CONTRIBUTING.md](CONTRIBUTING.md)에는 개발과 릴리스 재현 절차가 있습니다.
- [SECURITY.md](SECURITY.md)에는 비공개 취약점 신고 정책이 있습니다.
- [LICENSE](LICENSE)에는 Apache License 2.0 전문이 있습니다.

## 기여자 참고

- SDK DLL, 환경 파일, 로컬 MCP 설정, 캐시, 빌드 결과, 컴퓨터별 검증 로그를
  커밋하지 마세요.
- 개인 경로, 이메일 주소, 자격 증명, 사용자 파일 내용을 예시나 공개 결과물에
  포함하지 마세요.
- `EVERYTHING_EXE`, `EVERYTHING_SDK_DLL`, `EVERYTHING_ES_EXE`는 신뢰할 수 있는
  로컬 Everything 바이너리만 가리키게 설정하세요.
- 명령, 링크, 배지, 요구 사항, 릴리스 설명을 변경할 때는 `README.md`와
  `README.ko.md`의 구조를 함께 맞추세요.
- 이 프로젝트는 Apache License 2.0으로 배포합니다.
