# Contributing / 기여 안내

Everything_Mew is a Windows-only project supporting CPython 3.11 through 3.14.
Keep changes read-only, narrowly scoped, and free of local paths, downloaded
DLLs, credentials, caches, and validation evidence.

Everything_Mew는 CPython 3.11부터 3.14까지 지원하는 Windows 전용
프로젝트입니다. 변경은 읽기 전용 경계를 지키고 필요한 범위로 제한하며, 로컬
경로, 다운로드한 DLL, 자격 증명, 캐시, 검증 증거를 포함하지 않아야 합니다.

## Development setup / 개발 환경

Run from the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev,server]"
```

CI installs the optional FastMCP dependency so the retained standard entrypoints
are registration-tested. The direct lite stdio entrypoints themselves do not
require FastAPI or FastMCP.

CI는 선택 사항인 FastMCP 의존성도 설치해 기존 표준 진입점의 도구 등록을
검증합니다. 직접 stdio를 처리하는 lite 진입점 자체에는 FastAPI와 FastMCP가
필요하지 않습니다.

## Required checks / 필수 검사

Run the same checks used by CI:

```powershell
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m mypy --strict src tests
& .\.venv\Scripts\python.exe -m build
```

Construct the real FastMCP server and inspect its registrations without calling
`run()` or starting stdio:

```powershell
@'
import asyncio
from everything_mcp.server import create_mcp

server = create_mcp()
tools = asyncio.run(server.list_tools())
assert {tool.name for tool in tools} == {
    "everything_status",
    "everything_count",
    "everything_search",
    "everything_syntax_help",
}
'@ | & .\.venv\Scripts\python.exe -
```

CI repeats these commands on Windows with Python 3.11 and 3.14. Both a wheel
and source distribution must be present in `dist`.

CI는 Windows의 Python 3.11과 3.14에서 같은 명령을 반복합니다. `dist`에는
wheel과 source distribution이 모두 생성되어야 합니다.

## Installed-wheel smoke / 설치 wheel 스모크 테스트

Use a fresh venv, install only the built wheel, clear `PYTHONPATH`, and invoke
the installed lite console entrypoint:

```powershell
$wheel = @(Get-ChildItem -LiteralPath dist -Filter *.whl)
if ($wheel.Count -ne 1) { throw "Expected exactly one wheel." }

py -3.11 -m venv .release-smoke
& .\.release-smoke\Scripts\python.exe -m pip install --no-deps --no-index $wheel[0].FullName
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue

@'
import json
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import everything_mcp

package_path = Path(everything_mcp.__file__).resolve()
assert package_path.is_relative_to(Path(sys.prefix).resolve()), package_path
entrypoint = Path(sys.executable).with_name("everything-mew-lite.exe")
messages = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "release-smoke", "version": "1"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]
environment = os.environ.copy()
environment.pop("PYTHONPATH", None)
completed = subprocess.run(
    [str(entrypoint)],
    input="\n".join(json.dumps(message) for message in messages) + "\n",
    text=True,
    capture_output=True,
    check=True,
    env=environment,
)
assert not completed.stderr, completed.stderr
responses = [json.loads(line) for line in completed.stdout.splitlines() if line]
assert [response["id"] for response in responses] == [1, 2], responses
assert responses[0]["result"]["serverInfo"]["version"] == version("everything-mew")
assert {tool["name"] for tool in responses[1]["result"]["tools"]} == {
    "everything_status",
    "everything_count",
    "everything_search",
    "everything_syntax_help",
}
print("installed-wheel lite stdio smoke passed")
'@ | & .\.release-smoke\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

새 venv에 빌드된 wheel만 설치하고 `PYTHONPATH`를 제거한 뒤, 설치된 lite
콘솔 진입점으로 `initialize`, `notifications/initialized`, `tools/list`를
검증합니다.

## Artifact review / 산출물 검토

List every archive member before release:

```powershell
@'
from pathlib import Path
import tarfile
import zipfile

wheel = next(Path("dist").glob("*.whl"))
sdist = next(Path("dist").glob("*.tar.gz"))
with zipfile.ZipFile(wheel) as archive:
    wheel_members = archive.namelist()
with tarfile.open(sdist, "r:gz") as archive:
    sdist_members = archive.getnames()

for label, members in (("wheel", wheel_members), ("sdist", sdist_members)):
    print(f"{label}: {len(members)} files")
    print("\n".join(members))
    lowered = [member.lower().replace("\\", "/") for member in members]
    forbidden = (
        "skills/",
        ".github/",
        ".env",
        "_nonrelease",
        ".superpowers",
        ".dll",
        ".exe",
        "sdk_design_review.md",
    )
    assert not any(marker in member for member in lowered for marker in forbidden)
'@ | & .\.venv\Scripts\python.exe -
```

Also inspect archive text for credentials, tokens, personal paths, and local
validation evidence. The wheel and sdist must not contain `skills/`, SDK DLLs,
`.env` files, `_nonrelease/`, `.superpowers/`, CI internals, or stale design and
planning documents. The sdist includes the tests and the public SDK install
guide so pytest and mypy can be reproduced from source. The agent skill is
installed explicitly from this repository or from a plugin, never from the
Python wheel.

아카이브 텍스트에 자격 증명, 토큰, 개인 경로, 로컬 검증 증거가 없는지도
확인하세요. wheel과 sdist에는 `skills/`, SDK DLL, `.env`, `_nonrelease/`,
`.superpowers/`, CI 내부 파일, 오래된 설계/계획 문서가 포함되면 안 됩니다.
sdist에는 소스에서 pytest와 mypy를 재현할 수 있도록 테스트와 공개 SDK 설치
가이드를 포함합니다. 에이전트 스킬은 Python wheel이 아니라 이 저장소 또는
플러그인에서 명시적으로 설치합니다.

## Release reproduction / 릴리스 재현

1. Start from a clean worktree and install the development dependencies above.
2. Run the required checks on Python 3.11 and 3.14.
3. Delete stale local `dist` outputs, then run `python -m build` once.
4. Review both archives and run the installed-wheel smoke.
5. On a release branch only, update `[project].version` in `pyproject.toml`.
   Do not duplicate the version in Python; installed metadata and the structural
   source fallback use that single value.
6. Do not publish or tag until the reviewed artifacts come from the intended
   release commit.

Release naming uses PEP 440 for packages and SemVer-style Git tags. The first
0.2 candidate is package `0.2.0rc1` with tag `v0.2.0-rc.1`; the approved final
release is package `0.2.0` with tag `v0.2.0`. The historical `0.1.0` tag must
point to baseline commit `ffa5eaebaad5524c0bb35a90cf983e66cb9b452d`.

1. 깨끗한 worktree에서 위 개발 의존성을 설치합니다.
2. Python 3.11과 3.14에서 필수 검사를 실행합니다.
3. 오래된 로컬 `dist` 산출물을 삭제한 뒤 `python -m build`를 한 번 실행합니다.
4. 두 아카이브를 검토하고 설치 wheel 스모크 테스트를 실행합니다.
5. 릴리스 브랜치에서만 `pyproject.toml`의 `[project].version`을 변경합니다.
   Python 코드에 버전을 중복하지 않습니다.
6. 검토한 산출물이 의도한 릴리스 커밋에서 생성되기 전에는 게시하거나 태그를
   만들지 않습니다.

패키지 버전은 PEP 440, Git 태그는 SemVer 형식을 사용합니다. 첫 0.2 후보는
패키지 `0.2.0rc1`과 태그 `v0.2.0-rc.1`, 승인된 최종판은 패키지 `0.2.0`과
태그 `v0.2.0`을 사용합니다. 과거 `v0.1.0` 태그는 기준 커밋
`ffa5eaebaad5524c0bb35a90cf983e66cb9b452d`를 가리켜야 합니다.
