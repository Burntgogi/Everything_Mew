<p align="center">
  <img src="docs/assets/everything-mew-banner.png" width="420" alt="AI 에이전트의 파일 경로 탐색을 돕는 Everything_Mew 고양이 마스코트">
</p>

<h1 align="center">Everything_Mew</h1>

<p align="center"><strong>Everything을 이용해 Windows 파일을 빠르게 찾는 가벼운 읽기 전용 MCP 연결 도구입니다.</strong></p>

<p align="center">
  <a href="https://github.com/Burntgogi/Everything_Mew/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Burntgogi/Everything_Mew/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/Burntgogi/Everything_Mew/releases/tag/v0.3.0"><img alt="안정판 v0.3.0" src="https://img.shields.io/badge/stable-v0.3.0-5865F2"></a>
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows-0078D4">
  <img alt="Python 3.11부터 3.14" src="https://img.shields.io/badge/Python-3.11--3.14-3776AB">
  <img alt="읽기 전용 MCP 도구" src="https://img.shields.io/badge/MCP-read--only-1F883D">
  <a href="LICENSE"><img alt="Apache-2.0 라이선스" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="#빠른-시작">빠른 시작</a> ·
  <a href="#도구">도구</a> ·
  <a href="#구조">구조</a> ·
  <a href="#안전-경계">안전 경계</a> ·
  <a href="#릴리스-이력">릴리스 노트</a>
</p>

## 개요

Everything_Mew는 AI 에이전트를 로컬
[Everything](https://www.voidtools.com/) 인덱스에 연결합니다. 더 많은 비용이
드는 콘텐츠 도구로 파일을 읽기 전에 이름, 경로, 확장자, 크기, 날짜, 속성으로
후보 파일과 폴더를 찾습니다.

이 프로젝트는 Windows 전용이며 읽기 전용 MCP 도구 네 개를 제공합니다. 파일을
수정하거나 Everything 인덱스를 변경하지 않으며 Everything HTTP 서버도
활성화하지 않습니다. 저장소에는 간결한 Everything 문법을 안내하는 선택적
에이전트 스킬도 포함되어 있습니다. Python wheel은 MCP 런타임과 콘솔
진입점만 설치합니다.

## 작동 방식

Everything_Mew를 토큰 사용량이 적은 후보 탐색 계층으로 사용한 뒤, 선택한
경로는 일반 콘텐츠 또는 코드 도구로 확인하세요.

```text
everything_count -> everything_search -> read/grep/ast-grep/LSP on selected paths
```

- `everything_count`는 경로 목록을 만들지 않고 결과 규모를 확인합니다.
- `everything_search`는 경로 중심의 간결한 후보와 선택적 메타데이터를
  반환합니다.
- 파일 내용과 코드 의미는 `read`, `grep`, `ast-grep`, LSP로 확인하세요.

Codex에는 에이전트 스킬과 `everything-mew-once` 사용을 권장합니다. 요청할
때마다 수명이 짧은 프로세스 트리 하나가 실행되어 결과 하나를 반환한 뒤
종료하므로 사용하지 않을 때는 Everything_Mew Python 프로세스가 남지 않습니다.
공유 인덱서인 `Everything.exe`는 계속 실행됩니다. OpenCode와 수동 MCP
호스트에서는 호환 모드인 `everything-mew-lite`를 계속 사용할 수 있으며,
호스트 작업마다 서버 프로세스 트리 하나가 유지될 수 있습니다. one-shot 방식은
이 유휴 중복을 없애지만 실제 검색 중 최대 메모리가 일정하다고 보장하지는
않습니다.

## 빠른 시작

### 1. Everything과 Python을 준비하세요

다음 환경이 필요합니다.

- Windows에 표준 Everything 애플리케이션을 설치하고 실행해야 합니다.
- CPython 3.11부터 3.14까지 지원합니다.
- Python 비트수와 일치하는 공식 Everything SDK DLL이 필요합니다. 64비트
  Python에는 `Everything64.dll`, 32비트 Python에는 `Everything32.dll`을
  사용하세요.

Everything Lite는 필요한 IPC 인터페이스를 제공하지 않으므로 지원하지
않습니다. [공식 Everything SDK 페이지](https://www.voidtools.com/support/everything/sdk/)에서
SDK를 내려받아 신뢰할 수 있는 로컬 지원 디렉터리에 DLL을 보관하세요. 이
저장소와 Python 패키지에는 DLL이 포함되어 있지 않습니다.

### 2. 허용 경로 제한이 포함된 소스 리비전을 설치하세요

아래 정책 설정에는 [PR #8](https://github.com/Burntgogi/Everything_Mew/pull/8)의
미출시 변경이 필요합니다. 최신 안정 태그인 `v0.3.0`에는 이 설정을 적용하는
기능이 없습니다. 이 안내를 따를 때는 `main` 소스를 설치하세요.

```powershell
git clone https://github.com/Burntgogi/Everything_Mew.git
cd Everything_Mew
git checkout --detach origin/main
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -c "from everything_mcp.policy import SearchPolicy; assert SearchPolicy().denial_reason(None) is not None; print('Allowed-root policy available')"
```

정책 확인에 실패하면 검색 호스트를 활성화하기 전에 위 리비전에서 다시
설치하세요. 패키지 버전은 여전히 `0.3.0`이므로 버전 번호만으로 정책 기능의
설치 여부를 판단할 수 없습니다.

### 3. Codex에서 one-shot 실행기를 사용하세요

아래 DLL 값은 예시 자리표시자입니다. 사용 중인 컴퓨터에서 선택한 신뢰할 수
있는 SDK DLL의 정확한 경로를 순방향 슬래시로 입력하세요. DLL 비트수는 Python과
일치해야 합니다. 설치 명령을 Codex 프로세스에서 찾을 수 있게 한 뒤 호출할
때마다 절대 경로로 확인하세요.

```powershell
$env:EVERYTHING_SDK_DLL = "C:/replace/with/the/selected/sdk-dll"
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
EVERYTHING_SDK_DLL = "C:/replace/with/the/selected/sdk-dll"
EVERYTHING_MCP_ALLOWED_ROOTS = '["C:\\Work\\project"]'
```

`enabled = false`는 MCP를 사용할 수 없게 하는 설정이며, 필요할 때 자동으로
깨우는 수면 모드가 아닙니다. 이 설정을 바꾼 뒤에는 Codex를 완전히
재시작하세요. 이미 실행 중인 Codex 또는 OpenCode 호스트가 소유한 서버는 해당
호스트가 종료될 때만 함께 종료됩니다. 검색하기 전에 one-shot
`everything_status` 결과에서 백엔드가 `sdk-ipc`인지, 데이터베이스가
로드되었는지, 대상 아키텍처가 Python과 일치하는지 확인하세요.

OpenCode에서는 부모 프로세스의 환경 변수에 SDK 경로를 설정하고 다음과 같이
환경 변수 자리표시자를 그대로 사용하세요.

```json
{
  "mcp": {
    "everything-mew": {
      "type": "local",
      "command": ["everything-mew-lite"],
      "enabled": true,
      "environment": {
        "EVERYTHING_SDK_DLL": "{env:EVERYTHING_SDK_DLL}",
        "EVERYTHING_MCP_ALLOWED_ROOTS": "{env:EVERYTHING_MCP_ALLOWED_ROOTS}"
      }
    }
  }
}
```

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
| `everything_count` | 후보 경로를 반환하기 전에 범위가 제한된 쿼리의 결과 수를 확인합니다. |
| `everything_search` | 선택적 메타데이터와 함께 경로 중심의 간결한 후보를 반환합니다. |
| `everything_syntax_help` | Everything 버전을 고려한 간결한 쿼리 문법을 안내합니다. |

도구 네 개는 모두 읽기 전용입니다. 공개 MCP 계약은 후보 경로를 찾는 단계까지만
담당합니다.

## 요구 사항

- **운영체제:** Windows가 필요합니다.
- **Everything:** 표준 애플리케이션을 설치하고 실행해야 합니다.
- **Everything SDK:** 기본 SDK/IPC 백엔드에는 Python의 32비트 또는 64비트
  아키텍처와 일치하는 신뢰할 수 있는 DLL이 필요합니다.
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
  -> SDK/IPC adapter
  -> Everything runtime
  -> Existing Everything index
  -> compact path candidates
  -> read/grep/ast-grep/LSP for selected files
```

기본 어댑터는 공식 비동기 SDK 응답 창 흐름, 최대 15초 대기, 프로세스 전체 SDK
직렬화, 고유 응답 ID, 작업 후 상태 초기화를 사용합니다.
`everything_count`는 결과 플래그를 0으로 두고 `Everything_SetMax(0)`을 호출해
전체 개수만 요청합니다.

one-shot 경로는 크기가 제한된 JSON 요청 하나를 검증하고 같은 도구
디스패처를 사용해 결과 하나를 반환한 뒤 종료합니다. 데몬, HTTP 리스너,
프로세스 풀을 추가하지 않습니다.

lite MCP 경로는 `initialize`, notification, `ping`, `tools/list`, `tools/call`에
필요한 작은 stdio 수명 주기를 직접 구현합니다. FastMCP 서버는 같은 도구 함수와
어댑터를 사용하는 선택적 호환 계층으로 유지합니다.

## 사용 예시

많은 후보 경로를 반환하기 전에 개수를 확인하세요.

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
dm:thisweek ext:py;md;json
size:>100mb
regex:"gr(a|e)y" ext:txt
```

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

`v0.3.0` 릴리스는 다음 로컬 검증을 통과했습니다.

- 로컬 Python 3.11에서 pytest 422건, Ruff, 소스·테스트 31개 파일에 대한
  strict mypy를 통과했습니다.
- 격리 설치한 wheel로 `PYTHONPATH`, FastAPI, FastMCP 없이 lite MCP와
  one-shot 수명 주기를 완료했습니다.
- lite 경로와 호환 경로에서 읽기 전용 도구 네 개의 계약을 확인했습니다.
- 실제 SDK one-shot 검색을 순차 10회, 동시 6회 수행한 뒤 남은
  Everything_Mew 프로세스가 없음을 확인했습니다.
- Windows를 실제로 재부팅한 뒤 실호출 전후 유휴 Everything_Mew Python
  프로세스가 0개임을 확인했습니다.
- 소스와 배포 파일에 자격 증명, 환경 파일, SDK 바이너리, 캐시, 컴퓨터별 경로
  증거가 없는지 검사했습니다.

푸시와 pull request에는 Windows GitHub Actions가 Python 3.11과 3.14 검증을
수행합니다. 최종 검증 결과는
[v0.3.0 릴리스 노트](docs/releases/v0.3.0.md)를 참조하세요. 재현
가능한 빌드와 설치 wheel 검증 명령은 [CONTRIBUTING.md](CONTRIBUTING.md)에서
확인하세요.

## 릴리스 이력

| 릴리스 계열 | 역할 | 패키지 버전 | Git 태그 |
| --- | --- | --- | --- |
| 0.1 | 기존 FastMCP 기반 기준판 | `0.1.0` | `v0.1.0` |
| 0.2 | 안정 저대기 부담 MCP 릴리스 | `0.2.0` | `v0.2.0` |
| 0.3 | 현재 안정 one-shot 릴리스 | `0.3.0` | `v0.3.0` |

0.3은 제한형 one-shot 명령을 추가하고 이를 Codex 기본 사용 흐름으로
변경했습니다. OpenCode와 수동 MCP 호환용 lite stdio 서버를 유지하며 공개 도구
네 개의 계약도 그대로 유지합니다.

한영 릴리스 노트는 [v0.1.0](docs/releases/v0.1.0.md),
[v0.2.0](docs/releases/v0.2.0.md), 현재
[v0.3.0](docs/releases/v0.3.0.md) 릴리스 문서에서 확인하세요. 전체 한영
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
