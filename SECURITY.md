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

## Known trust limits / 알려진 신뢰 한계

These limits are part of the design and are not vulnerabilities by
themselves. Report a way to cross them, such as reading outside the allowed
roots.

- Any program that runs as the same Windows user can register the Everything
  IPC window class, read queries, and return false results. The SDK DLL and
  `es.exe` share this limit. Allowed roots still filter every returned path.
- File names are attacker-controlled text. Agents must treat returned paths as
  data, not instructions.
- When the agent is elevated and Everything is not, the reply window accepts
  `WM_COPYDATA` from a lower integrity level. Native IPC replies need a random
  32-bit reply ID.

다음 한계는 설계의 일부이며 그 자체로는 취약점이 아닙니다. 허용 경로 밖을
읽는 것처럼 이 한계를 넘는 방법을 신고하세요.

- 같은 Windows 사용자로 실행되는 프로그램은 Everything IPC 창 클래스를 등록해
  쿼리를 읽고 거짓 결과를 돌려줄 수 있습니다. SDK DLL과 `es.exe`도 같습니다.
  허용 경로는 이 경우에도 반환되는 모든 경로를 거릅니다.
- 파일 이름은 공격자가 정할 수 있는 텍스트입니다. 에이전트는 반환된 경로를
  지시가 아닌 데이터로 취급해야 합니다.
- 에이전트가 관리자 권한이고 Everything이 아니면, 응답 창은 낮은 무결성
  수준의 `WM_COPYDATA`를 받습니다. 네이티브 IPC 응답에는 무작위 32비트 응답
  ID가 필요합니다.
