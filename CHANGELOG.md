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
