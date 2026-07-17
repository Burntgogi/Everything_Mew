# Everything_Mew

언어: [English](README.md) | 한국어

<div align="center" aria-label="Everything_Mew 고양이 마스코트">
  <img src="https://raw.githubusercontent.com/Burntgogi/Everything_Mew/main/docs/assets/everything-mew-banner.png" width="420" alt="Everything_Mew: 인공지능의 검색을 돕는 작은 고양이">
</div>

**Everything_Mew**는 [Everything](https://www.voidtools.com/)을 이용해 AI 에이전트의 검색을 빠르게 돕는 Windows 전용 읽기 전용 MCP 서버와 OpenCode 스킬입니다.

컨셉은 **인공지능의 검색을 돕는 작은 고양이**입니다. 이 고양이는 파일을 대신 관리하지 않습니다. 후보 경로를 빠르게 찾아 주고, 실제 내용 확인은 일반 코드/콘텐츠 도구가 필요한 파일에만 수행하게 합니다.

## 무엇을 하나요?

Everything_Mew는 기존 Everything 인덱스를 빠르고 토큰을 적게 쓰는 후보 탐색 계층으로 사용합니다.

- 파일 또는 폴더 이름;
- 경로와 프로젝트 폴더;
- 확장자;
- 파일 크기;
- 생성일 또는 수정일;
- 파일 속성.

권장 흐름:

```text
everything_count -> everything_search -> read/grep/ast-grep/LSP on selected paths
```

Everything_Mew는 후보 발견을 위한 도구이며, 코드 이해, 중복 정리, 저장공간 관리를 위한 도구가 아닙니다.

## 릴리스 상태

| 릴리스 계열 | 역할 | 패키지 버전 | Git 태그 |
| --- | --- | --- | --- |
| 0.1 | 기존 FastMCP 기반 기준판 | `0.1.0` | `v0.1.0` |
| 0.2 | 현재 안정 릴리스 | `0.2.0` | `v0.2.0` |

`0.1.0` 기준판은 커밋 `ffa5eaebaad5524c0bb35a90cf983e66cb9b452d`입니다.
0.2 계열은 현재 안정 릴리스입니다. 한영 릴리스 노트는
[v0.1.0](docs/releases/v0.1.0.md)과 [v0.2.0](docs/releases/v0.2.0.md)을
참조하세요. 검토한 후보 기록은
[v0.2.0-rc.1](docs/releases/v0.2.0-rc.1.md)에 보존합니다.

## 업데이트 노트

### 0.2.0: 낮은 대기 메모리 MCP 모드

이번 업데이트는 Codex Desktop처럼 활성 세션마다 서버 프로세스를 유지하는 MCP
호스트를 위해 낮은 대기 메모리용 stdio MCP 경로를 추가합니다.

- `everything-mew-lite`와 `everything-mcp-lite` 콘솔 진입점을 추가했습니다.
- 모듈 실행을 선호하는 호스트를 위해 `python -m everything_mcp.lite_stdio`를 제공합니다.
- lite 경로는 대기 중 FastMCP를 import하지 않고 `initialize`, `ping`, `tools/list`, `tools/call`에 응답합니다.
- lite 경로는 MCP 2025-11-25 도구 오류 경계를 따릅니다. 잘못된
  `tools/call` 요청과 알 수 없는 도구는 JSON-RPC `-32602`를 반환하고, 스키마에
  맞는 알려진 도구 호출의 인자·실행 실패는 `result.isError: true`로 반환합니다.
  명시적인 `id: null` 요청은 `-32600`이며, `id`가 없는 메시지는 notification으로
  유지합니다.
- Everything SDK/IPC 백엔드는 실제 도구가 호출될 때까지 로드를 지연합니다.
- 기존 `everything-mew`와 `everything-mcp` 진입점은 FastMCP 경로로 계속 사용할 수 있습니다.
- 패키지 최상위 import는 FastMCP 서버 모듈을 lazy-loading하도록 바뀌었습니다.
- 서버 버전은 설치된 패키지 메타데이터에서 읽고, 소스 체크아웃에서는 `pyproject.toml`을 구조적으로 파싱해 대체 값을 사용합니다.
- Windows CI에서 Python 3.11과 3.14, strict 타입 검사, 두 배포 형식, 설치 wheel의 lite 핸드셰이크를 검증합니다.
- `.gitattributes`로 저장소 텍스트를 LF로 고정해 Windows의
  `core.autocrlf` 설정과 관계없이 같은 커밋의 깨끗한 체크아웃이 wheel과
  sdist에 동일한 바이트를 제공하도록 했습니다.
- lite stdio 동작, 읽기 전용 도구 메타데이터, 어댑터 선택, 광범위 쿼리 안전장치, 문법 검증에 대한 계약/안전 테스트를 보강했습니다.
- 광범위 쿼리 검증은 인용된 항목, 부정, `< >` 그룹, 모든 `|` 대안을
  파싱하며, 잘못된 문법이나 전체 일치 대안은 실패 폐쇄 방식으로 거부합니다.
- 절대 scope는 끝에 `\`가 있는 인용된 재귀 폴더 검색어로 합성하고, 반환
  경로를 직렬화하기 전에 정규화된 경계를 다시 검사합니다.
  `C:\Work\project` scope는 `C:\Work\project-backup`을 포함하지 않습니다.
- `everything_count`는 request flag를 0으로 두고 `Everything_SetMax(0)`을
  호출해 경로 목록을 만들지 않고 `Everything_GetTotResults()`만 읽습니다.
- Everything 1.4 콘텐츠 함수와 알려진 Everything 1.5 content/ADS 별칭 및
  `from-disk:`는 가능한 각 분기에서 루트가 아닌 scope와 추가 인덱스 축소
  필터를 요구합니다.
- SDK 쿼리는 무제한 동기 DLL 호출 대신 공식 비동기 응답 창 API와 15초
  제한 메시지 대기를 사용합니다.
- 응답 식별자와 SDK 상태를 프로세스 전체에서 관리하고 어댑터 인스턴스 간에
  직렬화합니다. Windows가 창 핸들을 재사용해도 시간 초과된 응답은 이후
  쿼리와 일치하지 않으며, 창 정리에 실패하면 ctypes 콜백을 안전한 재시도를
  위해 보존합니다.
- 중첩 modifier를 포함한 정규식은 인덱스 축소 필터로 인정하지 않습니다.
  정규식이 있는 각 OR 분기에는 `ext:py` 같은 별도의 전체 일치가 아닌 인덱스
  필터가 필요하며, 루트가 아닌 scope나 다른 정규식·와일드카드 modifier만으로는
  충분하지 않습니다. 전체 일치 와일드카드도 축소 조건이 아니며, 공식 `<`,
  `>`, `<=`, `>=` 비교식은 `< >` 그룹과 함께 사용할 수 있습니다.
- ES 메타데이터 CSV를 UTF-8로 명시적으로 디코딩하고, SDK 결과 경로는 공식
  필요 길이 조회 후 정확한 크기로 복사합니다.

최종 패키지 버전은 `0.2.0`이고 Git 태그는 `v0.2.0`입니다.
`v0.2.0-rc.1` 후보가 릴리스 검증을 통과한 뒤 승격했습니다. 한영 변경 사항은
[변경 기록](CHANGELOG.md)을 참조하세요.

권장 적용 방식:

- Codex Desktop 또는 항상 켜 두는 로컬 MCP 호스트에는 `everything-mew-lite`를 사용하세요.
- FastMCP와 이미 잘 동작하거나 기존 FastMCP 런타임 동작이 필요한 호스트에는 `everything-mew`를 유지하세요.

소스 또는 최종 릴리스 태그를 체크아웃한 뒤 0.1.0에서 업그레이드합니다.

```powershell
py -m pip install --upgrade .
```

FastMCP 호환 경로가 필요할 때만 선택 사항을 함께 설치합니다.

```powershell
py -m pip install --upgrade ".[server]"
```

기존 `everything-mew`와 `everything-mcp` 호스트 설정은 계속 유효합니다.
대기 메모리가 낮은 경로를 사용하려면 명령을 `everything-mew-lite`로 바꾸거나
`python -m everything_mcp.lite_stdio`를 사용하세요.

## 검색 문법 호환성과 안전

Everything 1.4.1 핵심 문법을 기본 호환 프로필로 사용합니다. Everything 1.5
전용으로 문서화된 기능을 사용하기 전에는 `everything_status`로 실행 버전을
확인하세요. SDK는 검색 문자열을 전달하고, 실제 문법 해석은 실행 중인
Everything 프로세스가 담당합니다.

절대 디렉터리는 `scope` 인자로 전달합니다. 예를 들어
`scope="C:\Work\project"`는 재귀 폴더 경계인 `"C:\Work\project\"`로
합성됩니다. 전체 경로 부분 일치인 `path:"C:\Work\project"`로 대체하면
접두사가 같은 형제 폴더까지 포함될 수 있습니다.

content 별칭, byte-stream/ADS content 함수, `content*:` literal-tail 형식,
`binary:content:` 같은 중첩 modifier 체인, `from-disk:`는 느린 I/O입니다.
루트가 아닌 scope와 `ext:txt` 같은 별도 인덱스 필터를 함께 사용해야 합니다.
Everything 연산자나 공백이 들어간 정규식은
`regex:"gr(a|e)y" ext:txt`처럼 인용하세요. 명시적인 `< >` 그룹이 없으면
OR가 AND보다 먼저 평가됩니다.

## 요구 사항

- **Windows 전용입니다.** Everything은 Windows 검색 도구입니다.
- **Everything이 설치되어 있어야 하며 백그라운드에서 실행 중이어야 합니다.**
- **기본 SDK/IPC 백엔드를 사용하려면 공식 Everything SDK가 필요합니다.** voidtools에서 `Everything-SDK.zip`을 다운로드하고 Python 비트수와 맞는 DLL을 제공해야 합니다.
  - 32비트 Python -> `Everything32.dll`
  - 64비트 Python -> `Everything64.dll`
- 이 연동은 로컬 Everything IPC를 사용하므로 Everything HTTP 서버가 필요하지 않습니다.
- Everything Lite는 IPC를 허용하지 않으므로 지원하지 않습니다.
- CPython 3.11부터 3.14까지.
- 로컬 stdio MCP를 지원하는 OpenCode 또는 다른 MCP 호스트.

공식 출처:

- Everything: <https://www.voidtools.com/>
- Everything 1.4 호환 검색 안내: <https://www.voidtools.com/support/everything/searching/>
- Everything 1.5 검색 문법: <https://www.voidtools.com/ko-kr/support/everything/search_syntax/>
- Everything 1.5 검색 modifier: <https://www.voidtools.com/ko-kr/support/everything/search_modifiers/>
- Everything 1.5 검색 함수: <https://www.voidtools.com/ko-kr/support/everything/search_functions/>
- Everything SDK: <https://www.voidtools.com/support/everything/sdk/>
- Everything_SetMax: <https://www.voidtools.com/support/everything/sdk/everything_setmax/>
- Everything_GetTotResults: <https://www.voidtools.com/support/everything/sdk/everything_gettotresults/>
- SDK 다운로드: <https://www.voidtools.com/Everything-SDK.zip>

## 설치

1. Windows에 Everything을 설치하고 실행합니다.
2. 공식 Everything SDK를 다운로드하고 압축을 풉니다.
3. `Everything64.dll` 또는 `Everything32.dll`을 신뢰할 수 있는 로컬 지원 디렉터리에 둡니다.
4. 권장 lite 진입점용 런타임 전용 패키지를 설치합니다.

   ```powershell
   py -m pip install -e .
   ```

   FastMCP 경로가 필요한 경우에만 선택 사항을 추가해 설치합니다.

   ```powershell
   py -m pip install -e ".[server]"
   ```

5. MCP 호스트에 로컬 stdio MCP 서버를 등록합니다.

빌드된 Python wheel에는 MCP 런타임과 콘솔 진입점만 포함됩니다.
[`skills/everything/SKILL.md`](skills/everything/SKILL.md)를 에이전트 호스트에
설치하지 않습니다. 이 스킬은 저장소 또는 스킬을 패키징한 플러그인에서
명시적으로 설치하세요. 선택 사항인 `[server]` extra는 표준 진입점용
FastMCP를 설치하며, lite 진입점은 런타임 전용 wheel만으로 동작합니다. 소스
배포 파일에는 테스트 모음과 공개 SDK 설치 가이드도 포함하지만 내부 설계,
감사, 계획 문서는 릴리스 산출물에 포함하지 않습니다.

OpenCode 스타일 등록 예시:

```json
{
  "mcp": {
    "everything-mew": {
      "type": "local",
      "command": ["everything-mew-lite"],
      "enabled": true,
      "environment": {
        "EVERYTHING_SDK_DLL": "{env:EVERYTHING_SDK_DLL}"
      }
    }
  }
}
```

OpenCode는 호스트 환경 변수를 `{env:VARIABLE_NAME}` 형식으로만 치환합니다.
OpenCode를 시작하기 전에 부모 PowerShell에서 `EVERYTHING_SDK_DLL`을
설정하세요. OpenCode가 값을 원시 JSON에 삽입하므로 환경 변수 값에는 순방향
슬래시를 사용해야 합니다. 64비트 Python에는 `Everything64.dll`, 32비트
Python에는 `Everything32.dll`을 선택하세요.

```powershell
$dllName = "Everything64.dll" # 32비트 Python에서는 Everything32.dll을 사용합니다.
$dllPath = Join-Path $env:USERPROFILE ".config\opencode\mcp-bin\everything-sdk\$dllName"
$env:EVERYTHING_SDK_DLL = $dllPath.Replace("\", "/")
opencode
```

이 기본 예시는 낮은 대기 메모리 진입점을 사용합니다. FastMCP 진입점
`everything-mew`와 기존 명령 이름 `everything-mcp`, `everything-mcp-lite`는
호환성 또는 명시적 선택을 위해 계속 제공합니다.

Codex Desktop처럼 활성 세션마다 stdio MCP 프로세스를 유지하는 호스트에서는
낮은 대기 메모리용 진입점을 우선 사용하세요.

```text
everything-mew-lite
```

`everything-mew-lite`는 stdio에서 작은 MCP 도구 표면을 직접 처리합니다.
FastAPI를 시작하거나 요구하지 않으며 FastMCP도 필요하지 않습니다. Everything
백엔드는 실제 도구 호출 시점에만 불러옵니다. editable 설치 후 콘솔 스크립트가
아직 재생성되지 않았다면 같은 환경 변수와 함께
`python -m everything_mcp.lite_stdio`를 사용할 수 있습니다.

낮은 대기 메모리 모드의 변경 사항:

- `everything-mew-lite`와 `everything-mcp-lite` 콘솔 진입점을 추가했습니다.
- 모듈 실행을 선호하는 호스트를 위해 `python -m everything_mcp.lite_stdio`를 추가했습니다.
- 패키지 import 시 FastMCP 서버 모듈을 바로 불러오지 않도록 lazy-loading으로 바꿨습니다.
- MCP `initialize` 응답에 읽기 전용 서버 지침을 포함합니다.
- 백엔드를 불러오기 전에 MCP `params`와 도구 인자 타입을 검증합니다.
- 선택 사항인 FastMCP 경로용 `everything-mew`와 `everything-mcp`를 계속 제공합니다.

## 저장소 구성

- [`src/everything_mcp/`](src/everything_mcp/): Python MCP 서버 구현.
- [`skills/everything/SKILL.md`](skills/everything/SKILL.md): OpenCode 스킬 지침.
- [`opencode.example.json`](opencode.example.json): MCP 등록 예시.
- [`docs/AGENT_INSTALLATION_GUIDE.md`](docs/AGENT_INSTALLATION_GUIDE.md): 에이전트용 안전 설치 가이드.
- [`docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md`](docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md): SDK 중심 설치 안내.
- [`docs/releases/`](docs/releases/): GitHub 게시용 한영 릴리스 노트 초안.
- [`CHANGELOG.md`](CHANGELOG.md): 한영 예정/배포 변경 기록.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): 기여 및 릴리스 재현 명령.
- [`SECURITY.md`](SECURITY.md): 비공개 취약점 신고 정책.
- [`LICENSE`](LICENSE): Apache License 2.0.

계획 문서, 워크플로 초안, 로컬 검증 증거, 장비별 메모는 배포 파일에서 제외하고 `_nonrelease/` 아래에 보관해야 합니다.

## 구조

핵심 작동 구조:

```text
AI agent
  -> Everything_Mew MCP tools
  -> SDK/IPC adapter
  -> Everything runtime
  -> Existing Everything index
  -> compact path candidates
  -> read/grep/ast-grep/LSP for selected files
```

도구 역할:

- `everything_status`: Everything과 선택된 백엔드가 준비되었는지 보고합니다.
- `everything_count`: 넓거나 모호한 쿼리에서 경로를 반환하기 전에 결과 규모를 확인합니다.
- `everything_search`: 경로 우선의 간결한 후보를 반환하며, 필요하면 메타데이터도 포함합니다.
- `everything_syntax_help`: Everything 쿼리 문법을 짧게 안내합니다.

Everything_Mew는 후보 발견까지만 담당합니다. 파일 내용과 코드 의미 분석은 `read`, `grep`, `ast-grep`, LSP가 맡습니다.

## 사용법

인덱싱된 파일시스템 메타데이터로 후보 파일이나 폴더를 찾아야 할 때 Everything_Mew를 사용합니다.

좋은 쿼리 예시:

```text
C:\Work\project\ ext:md
C:\Work\project\ config ext:json;yml;yaml !node_modules !.git
%USERPROFILE%\.config\opencode\ settings ext:json;md
C:\Work\ dm:thisweek ext:py;md;json
```

후보를 찾은 뒤에는 다른 도구로 전환합니다.

- 정확한 파일 내용은 `read`;
- 파일 안의 텍스트는 `grep` 또는 ripgrep;
- 구문 인식 코드 패턴은 `ast-grep`;
- 정의, 참조, 심볼, 진단은 LSP.

## 안전 경계

Everything_Mew는 읽기 전용입니다. 다음을 제안하거나 수행하면 안 됩니다.

- 삭제, 이동, 이름 변경, 격리, 정리 작업;
- 중복 제거 워크플로;
- Everything 인덱스 변경;
- 전체 드라이브 결과 덤프;
- 자동 HTTP 활성화;
- Everything 설정 쓰기.

## 검증

테스트 실행:

```powershell
py -m pytest -q
```

MCP 진입점 스모크 체크:

```powershell
everything-mew
```

`everything-mew`는 stdio MCP 서버를 시작하고 stdin/stdout에서 MCP 프로토콜 메시지를 기다립니다. 일반 터미널에서는 중단하기 전까지 멈춘 것처럼 보일 수 있습니다. 실제 도구 호출은 MCP 클라이언트 또는 검사기를 사용하세요.

낮은 대기 메모리 진입점 스모크 체크:

```powershell
everything-mew-lite
```

`everything-mew-lite`도 같은 stdin/stdout MCP 전송을 사용하지만, 대기 중에는 FastMCP를 로드하지 않습니다. `initialize`, `ping`, `tools/list`, 그리고 네 가지 읽기 전용 도구의 `tools/call`에 응답해야 합니다.

권장 릴리스 검증:

```powershell
py -m pytest -q
py -m ruff check .
py -m mypy --strict src tests
py -m build
```

새 venv를 사용하는 설치 wheel 스모크 및 산출물 검사 명령은
[CONTRIBUTING.md](CONTRIBUTING.md)에 있습니다. `PYTHONPATH`를 제거하고 설치된
`everything-mew-lite` 진입점을 실행해 `initialize`,
`notifications/initialized`, `tools/list`, 설치 메타데이터 버전, 네 가지 도구를
검증합니다.

예상 도구 목록:

```text
everything_status
everything_count
everything_search
everything_syntax_help
```

## 기여자 참고

- SDK DLL, `.env`, 로컬 MCP 설정, 캐시, 빌드 산출물, 장비별 검증 로그는 커밋하지 마세요.
- 예시와 문서에 개인 경로를 넣지 마세요.
- `EVERYTHING_EXE`, `EVERYTHING_SDK_DLL`, `EVERYTHING_ES_EXE`는 신뢰할 수 있는 로컬 Everything 바이너리만 가리켜야 합니다.
- 이 프로젝트는 Apache License 2.0으로 배포됩니다.
