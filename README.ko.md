# Everything_Mew

언어: [English](README.md) | 한국어

<div align="center" aria-label="Everything_Mew 고양이 마스코트">
  <img src="docs/assets/everything-mew-banner.png" width="420" alt="Everything_Mew: 인공지능의 검색을 돕는 작은 고양이">
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

## 요구 사항

- **Windows 전용입니다.** Everything은 Windows 검색 도구입니다.
- **Everything이 설치되어 있어야 하며 백그라운드에서 실행 중이어야 합니다.**
- **기본 SDK/IPC 백엔드를 사용하려면 공식 Everything SDK가 필요합니다.** voidtools에서 `Everything-SDK.zip`을 다운로드하고 Python 비트수와 맞는 DLL을 제공해야 합니다.
  - 32비트 Python -> `Everything32.dll`
  - 64비트 Python -> `Everything64.dll`
- 이 연동은 로컬 Everything IPC를 사용하므로 Everything HTTP 서버가 필요하지 않습니다.
- Everything Lite는 IPC를 허용하지 않으므로 지원하지 않습니다.
- Python 3.11 이상.
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

## 저장소 구성

- [`src/everything_mcp/`](src/everything_mcp/): Python MCP 서버 구현.
- [`skills/everything/SKILL.md`](skills/everything/SKILL.md): OpenCode 스킬 지침.
- [`opencode.example.json`](opencode.example.json): MCP 등록 예시.
- [`docs/AGENT_INSTALLATION_GUIDE.md`](docs/AGENT_INSTALLATION_GUIDE.md): 에이전트용 안전 설치 가이드.
- [`docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md`](docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md): SDK 중심 설치 안내.
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

예상 도구 목록:

```text
everything_status
everything_count
everything_search
everything_syntax_help
```

## 배포 체크리스트

- `_nonrelease/`, 캐시, 가상환경, 빌드 산출물, SDK DLL, 로컬 MCP 설정은 Git에 올리지 않습니다.
- 개인 경로, 로컬 검증 로그, 백업, 장비별 증거를 커밋하지 않습니다.
- 라이선스는 Apache License 2.0입니다. 재배포 시 저작권과 라이선스 고지를 유지해야 합니다.
- `v0.1.0` 같은 semver 릴리스 태그를 사용합니다.

## 신뢰할 수 있는 로컬 경로

`EVERYTHING_SDK_DLL`과 `EVERYTHING_ES_EXE`는 신뢰할 수 있는 로컬 설정 값입니다. 직접 설치한 Everything SDK/ES 바이너리만 가리키세요. 다운로드했거나 신뢰할 수 없는 실행 파일을 가리키면 안 됩니다.
