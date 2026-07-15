# Changelog / 변경 기록

All notable release changes are documented here. / 주요 릴리스 변경 사항을 이 문서에 기록합니다.

## Unreleased (planned 0.2.0) / 미배포 (0.2.0 예정)

The package metadata remains at `0.1.0` on the feature branch. The version will
change only when the release branch is cut.

기능 브랜치의 패키지 메타데이터는 `0.1.0`으로 유지합니다. 릴리스 브랜치를
만들 때만 버전을 변경합니다.

### English

- Add direct lite stdio entrypoints that handle the MCP lifecycle without
  requiring FastAPI or FastMCP.
- Resolve the lite server version from installed distribution metadata, with a
  structurally parsed `pyproject.toml` fallback for source checkouts.
- Add Windows CI for Python 3.11 and 3.14 covering pytest, Ruff, strict mypy,
  wheel and source builds, and an installed-wheel lite stdio smoke test.
- Clarify that Python distributions contain the MCP runtime only; the agent
  skill must be installed explicitly from the repository or a plugin.
- Add security reporting, contribution, SDK environment, and reproducible
  release guidance.
- Parse OR alternatives, grouping, quoting, and negation before broad-query
  checks; reject malformed and universal branches without calling a backend.
- Apply the expensive-content policy to every supported content function and
  every possible query branch.
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
- Make `everything-mew-lite` the primary always-on configuration example while
  retaining FastMCP as an optional compatibility path.

### 한국어

- FastAPI 또는 FastMCP 없이 MCP 수명 주기를 처리하는 직접 lite stdio
  진입점을 추가합니다.
- 설치된 배포 메타데이터에서 lite 서버 버전을 확인하고, 소스 체크아웃에서는
  `pyproject.toml`을 구조적으로 파싱해 대체 값을 읽습니다.
- Python 3.11과 3.14 Windows CI에서 pytest, Ruff, strict mypy, wheel/sdist
  빌드, 설치된 wheel의 lite stdio 스모크 테스트를 실행합니다.
- Python 배포 파일에는 MCP 런타임만 포함되며, 에이전트 스킬은 저장소 또는
  플러그인에서 명시적으로 설치해야 함을 분명히 합니다.
- 보안 신고, 기여, SDK 환경 변수, 재현 가능한 릴리스 절차를 문서화합니다.
- 광범위 쿼리 검사 전에 OR 대안, 그룹, 인용, 부정을 파싱하고, 잘못된 문법과
  전체 일치 분기는 백엔드를 호출하지 않고 거부합니다.
- 지원하는 모든 콘텐츠 함수와 가능한 각 쿼리 분기에 고비용 콘텐츠 정책을
  적용합니다.
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
- 항상 켜 두는 기본 설정 예시는 `everything-mew-lite`로 변경하고 FastMCP는
  선택 가능한 호환 경로로 유지합니다.
