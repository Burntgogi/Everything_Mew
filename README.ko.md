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

## 업데이트 노트

### 0.2.0 예정(미배포): 낮은 대기 메모리 MCP 모드

이번 업데이트는 Codex Desktop처럼 활성 세션마다 서버 프로세스를 유지하는 MCP
호스트를 위해 낮은 대기 메모리용 stdio MCP 경로를 추가합니다.

- `everything-mew-lite`와 `everything-mcp-lite` 콘솔 진입점을 추가했습니다.
- 모듈 실행을 선호하는 호스트를 위해 `python -m everything_mcp.lite_stdio`를 제공합니다.
- lite 경로는 대기 중 FastMCP를 import하지 않고 `initialize`, `ping`, `tools/list`, `tools/call`에 응답합니다.
- Everything SDK/IPC 백엔드는 실제 도구가 호출될 때까지 로드를 지연합니다.
- 기존 `everything-mew`와 `everything-mcp` 진입점은 FastMCP 경로로 계속 사용할 수 있습니다.
- 패키지 최상위 import는 FastMCP 서버 모듈을 lazy-loading하도록 바뀌었습니다.
- 서버 버전은 설치된 패키지 메타데이터에서 읽고, 소스 체크아웃에서는 `pyproject.toml`을 구조적으로 파싱해 대체 값을 사용합니다.
- Windows CI에서 Python 3.11과 3.14, strict 타입 검사, 두 배포 형식, 설치 wheel의 lite 핸드셰이크를 검증합니다.
- lite stdio 동작, 읽기 전용 도구 메타데이터, 어댑터 선택, 광범위 쿼리 안전장치, 문법 검증에 대한 계약/안전 테스트를 보강했습니다.

기능 브랜치의 패키지 버전은 `0.1.0`으로 유지하며, `0.2.0`은 아직 배포되지
않은 예정 버전입니다. 한영 릴리스 노트는 [변경 기록](CHANGELOG.md)을
참조하세요.

권장 적용 방식:

- Codex Desktop 또는 항상 켜 두는 로컬 MCP 호스트에는 `everything-mew-lite`를 사용하세요.
- FastMCP와 이미 잘 동작하거나 기존 FastMCP 런타임 동작이 필요한 호스트에는 `everything-mew`를 유지하세요.

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
- Everything SDK: <https://www.voidtools.com/support/everything/sdk/>
- SDK 다운로드: <https://www.voidtools.com/Everything-SDK.zip>

## 설치

1. Windows에 Everything을 설치하고 실행합니다.
2. 공식 Everything SDK를 다운로드하고 압축을 풉니다.
3. `Everything64.dll` 또는 `Everything32.dll`을 신뢰할 수 있는 로컬 지원 디렉터리에 둡니다.
4. 서버 지원을 포함해 패키지를 설치합니다.

   ```powershell
   py -m pip install -e ".[server]"
   ```

5. MCP 호스트에 로컬 stdio MCP 서버를 등록합니다.

빌드된 Python wheel에는 MCP 런타임과 콘솔 진입점만 포함됩니다.
[`skills/everything/SKILL.md`](skills/everything/SKILL.md)를 에이전트 호스트에
설치하지 않습니다. 이 스킬은 저장소 또는 스킬을 패키징한 플러그인에서
명시적으로 설치하세요. 선택 사항인 `[server]` extra는 표준 진입점용
FastMCP를 설치하며, lite 진입점은 런타임 전용 wheel만으로 동작합니다.

OpenCode 스타일 등록 예시:

```json
{
  "mcp": {
    "everything-mew": {
      "type": "local",
      "command": ["everything-mew"],
      "enabled": true,
      "environment": {
        "EVERYTHING_SDK_DLL": "%USERPROFILE%\\.config\\opencode\\mcp-bin\\everything-sdk\\Everything64.dll"
      }
    }
  }
}
```

호환성을 위해 기존 명령 이름 `everything-mcp`도 유지합니다.

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
- 기존 FastMCP 경로인 `everything-mew`와 `everything-mcp`는 그대로 유지합니다.

## 저장소 구성

- [`src/everything_mcp/`](src/everything_mcp/): Python MCP 서버 구현.
- [`skills/everything/SKILL.md`](skills/everything/SKILL.md): OpenCode 스킬 지침.
- [`opencode.example.json`](opencode.example.json): MCP 등록 예시.
- [`docs/AGENT_INSTALLATION_GUIDE.md`](docs/AGENT_INSTALLATION_GUIDE.md): 에이전트용 안전 설치 가이드.
- [`docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md`](docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md): SDK 중심 설치 안내.
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
