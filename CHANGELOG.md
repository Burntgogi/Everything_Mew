# Changelog / 변경 기록

All notable release changes are documented here. / 주요 릴리스 변경 사항을 이 문서에 기록합니다.

## [Unreleased]

## [0.3.0-rc.1] - 2026-08-13

The Python package version is `0.3.0rc1` and its Git tag is
`v0.3.0-rc.1`. This prerelease keeps the four public read-only tool contracts
while changing the recommended Codex execution model from a session-scoped MCP
server to a bounded one-shot process tree.

Python 패키지 버전은 `0.3.0rc1`, Git 태그는 `v0.3.0-rc.1`입니다. 이 시험판은
공개 읽기 전용 도구 네 개의 계약을 유지하면서 Codex 권장 실행 방식을 세션 단위
MCP 서버에서 제한형 one-shot 프로세스 트리로 변경합니다.

### English

- Add bounded `everything-mew-once` and `everything-mcp-once` commands that
  execute one existing read-only tool request and exit without FastMCP.
- Limit each request to one 65,536-byte JSON envelope and use stable exit codes
  for success, published tool errors, and invalid invocations.
- Make the Codex skill use the one-shot command by default so no
  Everything_Mew Python process remains while unused.
- Pin and validate absolute runner and SDK DLL paths in the installed Codex
  skill, with safe PowerShell literal escaping during installation.
- Keep `everything-mew-lite` for OpenCode and manual MCP compatibility, and
  document that a disabled MCP is unavailable rather than automatically asleep.

### 한국어

- 기존 읽기 전용 도구 요청 하나를 실행한 뒤 종료하는 제한형
  `everything-mew-once`와 `everything-mcp-once` 명령을 FastMCP 없이
  추가합니다.
- 요청 하나를 65,536바이트 JSON 봉투 하나로 제한하고 성공, 공개된 도구 오류,
  잘못된 호출을 안정적인 종료 코드로 구분합니다.
- 사용하지 않을 때 Everything_Mew Python 프로세스가 남지 않도록 Codex
  스킬의 기본 실행 경로를 one-shot 명령으로 변경합니다.
- 설치된 Codex 스킬에 실행기와 SDK DLL의 절대 경로를 고정·검증하고 설치 중
  PowerShell 리터럴을 안전하게 이스케이프합니다.
- OpenCode와 수동 MCP 호환용 `everything-mew-lite`를 유지하고, 비활성 MCP는
  자동 수면 상태가 아니라 사용할 수 없는 상태임을 문서화합니다.

## [0.2.0] - 2026-07-17

The final package version is `0.2.0` and its Git tag is `v0.2.0`. It was
promoted after the `v0.2.0-rc.1` candidate passed code review, local and CI
verification, archive inspection, installed-wheel smoke tests, and live SDK
E2E validation.

최종 패키지 버전은 `0.2.0`이고 Git 태그는 `v0.2.0`입니다.
`v0.2.0-rc.1` 후보가 코드 리뷰, 로컬·CI 검증, 아카이브 검사, 설치 wheel
스모크 테스트, 실제 SDK E2E를 통과한 뒤 승격했습니다.

### English

- Add direct lite stdio entrypoints that handle the MCP lifecycle without
  requiring FastAPI or FastMCP.
- Resolve the lite server version from installed distribution metadata, with a
  structurally parsed `pyproject.toml` fallback for source checkouts.
- Add Windows CI for Python 3.11 and 3.14 covering pytest, Ruff, strict mypy,
  wheel and source builds, and an installed-wheel lite stdio smoke test.
- Normalize repository text to LF with `.gitattributes`, preventing Windows
  `core.autocrlf` settings from changing wheel and sdist bytes between clean
  checkouts of the same commit.
- Clarify that Python distributions contain the MCP runtime only; the agent
  skill must be installed explicitly from the repository or a plugin.
- Add security reporting, contribution, SDK environment, and reproducible
  release guidance.
- Parse OR alternatives, grouping, quoting, and negation before broad-query
  checks; reject malformed and universal branches without calling a backend.
- Apply the expensive-content policy to every supported content function and
  every possible query branch.
- Use Everything 1.4.1 core syntax as the default profile, recognize known 1.5
  content/ADS aliases, `content*:` literal-tail forms, nested content modifier
  chains, and `from-disk:` as slow I/O, and require a separate indexed filter
  for each affected branch.
- Compose `C:\Work\project` scope as the exact recursive boundary
  `"C:\Work\project\"` and discard any adapter result outside that boundary.
- Make SDK count total-only in the IPC payload by setting zero request flags and
  `Everything_SetMax(0)` before reading `Everything_GetTotResults()`.
- Replace the unbounded synchronous SDK query with the official asynchronous
  reply-window flow and a 15-second deadline, serialized through reset.
- Allocate non-reused reply IDs process-wide, serialize SDK state across
  adapter instances, reject stale replies after HWND reuse, and retain failed
  Win32 callback cleanup for retry.
- Never treat regex, including nested modifier forms, as an indexed narrowing
  filter; require a separate indexed, non-universal term in every regex branch.
  Continue rejecting universal wildcard filters while preserving official
  size/date comparisons and grouped expressions.
- Decode ES metadata CSV as UTF-8 and retrieve SDK paths with the documented
  required-length call, including the exact 32,767-character limit.
- Validate the installed wheel against a live Everything 1.4.1.1032 index with
  `size:>100mb`: all 1,005 SDK results were unique, larger than 100 MiB, and
  size-sorted, while the public 100-item page exactly matched the SDK oracle.
  The path-bearing report remains local and is excluded from release artifacts.
- Make `everything-mew-lite` the primary always-on configuration example while
  retaining FastMCP as an optional compatibility path.
- Align lite `tools/call` errors with MCP 2025-11-25: malformed requests and
  unknown tools use JSON-RPC `-32602`, known-tool validation and execution
  failures use `isError: true`, and explicit null request IDs are invalid.

### 한국어

- FastAPI 또는 FastMCP 없이 MCP 수명 주기를 처리하는 직접 lite stdio
  진입점을 추가합니다.
- 설치된 배포 메타데이터에서 lite 서버 버전을 확인하고, 소스 체크아웃에서는
  `pyproject.toml`을 구조적으로 파싱해 대체 값을 읽습니다.
- Python 3.11과 3.14 Windows CI에서 pytest, Ruff, strict mypy, wheel/sdist
  빌드, 설치된 wheel의 lite stdio 스모크 테스트를 실행합니다.
- `.gitattributes`로 저장소 텍스트를 LF로 고정해 Windows의
  `core.autocrlf` 설정이 같은 커밋의 깨끗한 체크아웃 사이에서 wheel과 sdist
  바이트를 바꾸지 않도록 했습니다.
- Python 배포 파일에는 MCP 런타임만 포함되며, 에이전트 스킬은 저장소 또는
  플러그인에서 명시적으로 설치해야 함을 분명히 합니다.
- 보안 신고, 기여, SDK 환경 변수, 재현 가능한 릴리스 절차를 문서화합니다.
- 광범위 쿼리 검사 전에 OR 대안, 그룹, 인용, 부정을 파싱하고, 잘못된 문법과
  전체 일치 분기는 백엔드를 호출하지 않고 거부합니다.
- 지원하는 모든 콘텐츠 함수와 가능한 각 쿼리 분기에 고비용 콘텐츠 정책을
  적용합니다.
- Everything 1.4.1 핵심 문법을 기본 프로필로 사용하고, 알려진 1.5
  content/ADS 별칭, `content*:` literal-tail 형식, 중첩 content modifier 체인,
  `from-disk:`를 느린 I/O로 분류해 각 관련 분기에 별도 인덱스 필터를
  요구합니다.
- `C:\Work\project` scope를 정확한 재귀 경계 `"C:\Work\project\"`로
  합성하고 경계 밖의 어댑터 결과를 제거합니다.
- SDK count는 request flag 0과 `Everything_SetMax(0)`을 설정한 뒤
  `Everything_GetTotResults()`만 읽어 IPC payload에서 실제 total-only로
  동작하게 합니다.
- 무제한 동기 SDK 쿼리를 공식 비동기 응답 창 흐름과 15초 제한으로 교체하고,
  Reset까지 직렬화합니다.
- 프로세스 전체에서 재사용하지 않는 응답 ID를 할당하고 어댑터 간 SDK 상태를
  직렬화하며, HWND가 재사용되어도 오래된 응답을 거부하고 Win32 콜백 정리
  실패 객체를 재시도할 때까지 보존합니다.
- 중첩 modifier를 포함한 정규식은 인덱스 축소 필터로 인정하지 않고, 정규식이
  있는 모든 분기에 별도의 전체 일치가 아닌 인덱스 조건을 요구합니다. 전체
  일치 와일드카드는 계속 거부하며 공식 크기·날짜 비교식과 그룹식은 유지합니다.
- ES 메타데이터 CSV를 UTF-8로 디코딩하고, 문서화된 필요 길이 호출로 SDK
  경로를 가져와 정확히 32,767자인 경로까지 처리합니다.
- 설치 wheel을 실제 Everything 1.4.1.1032 인덱스의 `size:>100mb` 검색으로
  검증했습니다. SDK 결과 1,005건은 모두 중복이 없고 100 MiB를 초과하며 크기
  순으로 정렬됐고, 공개 100건 페이지는 SDK 원본과 정확히 일치했습니다. 경로가
  포함된 보고서는 로컬에만 보관하고 릴리스 산출물에서 제외합니다.
- 항상 켜 두는 기본 설정 예시는 `everything-mew-lite`로 변경하고 FastMCP는
  선택 가능한 호환 경로로 유지합니다.
- lite `tools/call` 오류를 MCP 2025-11-25에 맞춰 잘못된 요청과 알 수 없는
  도구는 JSON-RPC `-32602`, 알려진 도구의 검증·실행 실패는 `isError: true`로
  반환하고 명시적인 null 요청 ID는 거부합니다.

## [0.1.0] - 2026-04-26

Baseline commit: `ffa5eaebaad5524c0bb35a90cf983e66cb9b452d`.

### English

- Add the initial Windows-only, read-only Everything MCP server and OpenCode skill.
- Provide `everything_status`, `everything_count`, `everything_search`, and
  `everything_syntax_help` tools.
- Add SDK/IPC, ES CLI, and optional HTTP adapter selection, with SDK/IPC as the
  primary local backend.
- Ship the `everything-mew` command and retain `everything-mcp` as a compatibility
  alias through the optional FastMCP server dependency.
- Document Everything SDK installation, read-only boundaries, and the
  path-first discovery workflow.

### 한국어

- Windows 전용 읽기 전용 Everything MCP 서버와 OpenCode 스킬의 최초 판본을
  추가했습니다.
- `everything_status`, `everything_count`, `everything_search`,
  `everything_syntax_help` 도구를 제공합니다.
- SDK/IPC를 기본 로컬 백엔드로 사용하고 ES CLI와 선택적 HTTP 어댑터 선택을
  제공합니다.
- 선택 사항인 FastMCP 서버 의존성을 통해 `everything-mew` 명령을 제공하고
  `everything-mcp`를 호환 별칭으로 유지합니다.
- Everything SDK 설치, 읽기 전용 경계, 경로 우선 후보 탐색 흐름을
  문서화했습니다.

[0.3.0-rc.1]: https://github.com/Burntgogi/Everything_Mew/compare/v0.2.0...v0.3.0-rc.1
[0.2.0]: https://github.com/Burntgogi/Everything_Mew/compare/v0.1.0...v0.2.0
[0.2.0-rc.1]: https://github.com/Burntgogi/Everything_Mew/compare/v0.1.0...v0.2.0-rc.1
[0.1.0]: https://github.com/Burntgogi/Everything_Mew/releases/tag/v0.1.0
