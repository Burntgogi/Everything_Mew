from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def oneshot_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def run_oneshot(request: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "everything_mcp.oneshot"],
        cwd=ROOT,
        env=oneshot_environment(),
        input=json.dumps(request, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )


def test_oneshot_subprocess_executes_unicode_syntax_request_and_exits() -> None:
    process = run_oneshot(
        {
            "schemaVersion": 1,
            "tool": "everything_syntax_help",
            "arguments": {"topic": "필터"},
        }
    )

    result = json.loads(process.stdout)
    assert process.returncode == 0
    assert result["isError"] is False
    assert process.stderr == ""


def test_oneshot_subprocess_rejects_unknown_tool_without_traceback() -> None:
    process = run_oneshot(
        {"schemaVersion": 1, "tool": "everything_delete", "arguments": {}}
    )

    assert process.returncode == 2
    assert process.stdout == ""
    assert process.stderr == "ONESHOT_INVOCATION_ERROR\n"
    assert "Traceback" not in process.stderr


def test_oneshot_syntax_call_does_not_import_mcp_frameworks() -> None:
    probe = r'''
from io import BytesIO, StringIO
import json
import sys
from everything_mcp.oneshot import run

request = json.dumps({
    "schemaVersion": 1,
    "tool": "everything_syntax_help",
    "arguments": {"topic": "filters"},
}).encode("utf-8")
output = BytesIO()
code = run(BytesIO(request), output, StringIO())
print(json.dumps({
    "code": code,
    "isError": json.loads(output.getvalue())["isError"],
    "fastmcp": "fastmcp" in sys.modules,
    "mcp": "mcp" in sys.modules,
}))
'''
    process = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env=oneshot_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )

    assert process.returncode == 0
    assert json.loads(process.stdout) == {
        "code": 0,
        "isError": False,
        "fastmcp": False,
        "mcp": False,
    }
    assert process.stderr == ""


def test_pyproject_exposes_both_oneshot_console_scripts() -> None:
    with (ROOT / "pyproject.toml").open("rb") as pyproject_file:
        scripts = tomllib.load(pyproject_file)["project"]["scripts"]

    assert scripts["everything-mew-once"] == "everything_mcp.oneshot:main"
    assert scripts["everything-mcp-once"] == "everything_mcp.oneshot:main"
