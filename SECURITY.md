# Security Policy / 보안 정책

## Report a vulnerability

Report suspected vulnerabilities through
[GitHub private vulnerability reporting](https://github.com/Burntgogi/Everything_Mew/security/advisories/new).
Do not disclose an unpatched vulnerability in a public issue, discussion, pull
request, or chat transcript.

Include the affected version, Windows and Python versions, backend in use,
minimal reproduction steps, impact, and any known mitigation. Remove personal
paths, credentials, indexed filenames, and other unrelated private data before
submitting the report.

## 취약점 신고

의심되는 취약점은
[GitHub 비공개 취약점 신고](https://github.com/Burntgogi/Everything_Mew/security/advisories/new)를
통해 제출하세요. 패치되지 않은 취약점을 공개 이슈, 토론, pull request 또는
채팅 기록에 공개하지 마세요.

영향받는 버전, Windows/Python 버전, 사용 중인 백엔드, 최소 재현 절차, 영향,
알려진 완화 방법을 포함하세요. 제출 전에 개인 경로, 자격 증명, 인덱싱된 파일명
등 관련 없는 비공개 정보를 제거하세요.

## Supported releases / 지원 릴리스

| Release | Status |
| --- | --- |
| `0.4.x` | Current supported / 현재 지원 |
| `0.3.x` | Supported previous line, without allowed-root enforcement / 이전 계열 지원, 허용 경로 정책 미적용 |
| `0.2.x` | Unsupported; upgrade to `0.4.x` / 지원 종료, `0.4.x`로 업그레이드 |

Everything_Mew supports CPython 3.11 through 3.14 on Windows. Reports against
unsupported Python versions or non-Windows hosts may still be useful, but they
are outside the supported runtime matrix.

Everything_Mew는 Windows의 CPython 3.11부터 3.14까지 지원합니다. 지원 범위
밖의 Python 버전이나 Windows가 아닌 환경에 대한 신고도 참고할 수 있지만,
지원 런타임 매트릭스에는 포함되지 않습니다.

## Scope / 범위

Security-sensitive areas include read-only query boundaries, JSON-RPC input
validation, unintended path or exception disclosure, executable and DLL path
handling, dependency and build integrity, and accidental inclusion of secrets
or local evidence in release artifacts.

보안 검토 범위에는 읽기 전용 쿼리 경계, JSON-RPC 입력 검증, 의도하지 않은
경로/예외 노출, 실행 파일 및 DLL 경로 처리, 의존성과 빌드 무결성, 배포 산출물에
비밀 정보나 로컬 검증 자료가 포함되는 문제가 있습니다.

## Trust boundaries / 신뢰 경계

The native IPC backend enforces these boundaries. Report a way to cross one,
such as making it accept a reply from a program other than `EVERYTHING_EXE`.

- It sends queries only to an Everything window that the `EVERYTHING_EXE`
  process owns, and fails closed otherwise. Keep that executable in an
  administrator-protected directory such as Program Files. The forced `sdk`
  and `es` backends and `EVERYTHING_MCP_VERIFY_IPC_OWNER=0` skip this check.
- Its reply window accepts `WM_COPYDATA` across UIPI only when Everything runs
  at a lower integrity level than the agent, and each reply needs a random
  32-bit reply ID.
- File names are attacker-controlled text. The model-visible text escapes
  invisible and bidirectional characters, and the server instructions mark
  paths as untrusted data. Ordinary words in a file name cannot be removed
  without changing the data, so agents must still treat paths as data.

네이티브 IPC 백엔드는 다음 경계를 적용합니다. `EVERYTHING_EXE`가 아닌
프로그램의 응답을 받아들이게 만드는 것처럼 경계를 넘는 방법을 신고하세요.

- `EVERYTHING_EXE` 프로세스가 소유한 Everything 창에만 쿼리를 보내고, 아니면
  실패로 끝냅니다. 이 실행 파일은 Program Files처럼 관리자만 바꿀 수 있는
  디렉터리에 두세요. 강제 지정한 `sdk`·`es` 백엔드와
  `EVERYTHING_MCP_VERIFY_IPC_OWNER=0`은 이 확인을 건너뜁니다.
- 응답 창은 Everything이 에이전트보다 낮은 무결성 수준에서 실행될 때만 UIPI를
  넘는 `WM_COPYDATA`를 받으며, 응답마다 무작위 32비트 응답 ID가 필요합니다.
- 파일 이름은 공격자가 정할 수 있는 텍스트입니다. 모델에 보이는 텍스트는
  보이지 않는 문자와 양방향 제어 문자를 이스케이프하고, 서버 지침은 경로를
  신뢰할 수 없는 데이터로 표시합니다. 파일 이름 속 일반 단어는 데이터를 바꾸지
  않고는 없앨 수 없으므로, 에이전트는 여전히 경로를 데이터로 취급해야 합니다.
