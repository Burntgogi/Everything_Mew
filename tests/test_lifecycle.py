"""Process lifecycle, transport encoding, and failure signalling contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch

from everything_mcp import lite_stdio, server
from everything_mcp.contracts import SearchBatch, SearchHit, SortName
from everything_mcp.errors import BackendUnavailableError, QueryError
from everything_mcp.policy import SearchPolicy

SRC = Path(__file__).resolve().parents[1] / "src"
SCOPE = r"C:\Work"
POLICY = SearchPolicy(allowed_roots=(SCOPE,), allow_metadata=True)


class Adapter:
    name = "fake"

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def status(self) -> Any:  # pragma: no cover - not used
        raise AssertionError

    def count(self, query: str, scope: str | None = None) -> int:
        if self.error:
            raise self.error
        return int(self.result)

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: SortName = "name",
        metadata: bool = False,
    ) -> list[SearchHit] | SearchBatch:
        if self.error:
            raise self.error
        assert isinstance(self.result, SearchBatch)
        return self.result


@pytest.mark.parametrize(
    ("error", "code"),
    [(BackendUnavailableError("Everything is not running"), "backend_unavailable"), (QueryError("bad"), "query_failed")],
)
def test_backend_and_query_failures_are_tool_errors(error: Exception, code: str) -> None:
    search = server.everything_search("ext:py", SCOPE, adapter=Adapter(error=error), policy=POLICY)
    count = server.everything_count("ext:py", SCOPE, adapter=Adapter(error=error), policy=POLICY)

    assert search["error"]["code"] == count["error"]["code"] == code
    assert lite_stdio._tool_success(search)["isError"] is True
    assert lite_stdio._tool_success(count)["isError"] is True


def test_policy_denial_is_a_tool_error_and_never_reaches_the_backend() -> None:
    adapter = Adapter(error=AssertionError("backend must not run"))

    result = server.everything_search("ext:py", r"C:\Elsewhere", adapter=adapter, policy=POLICY)

    assert result["denied"] is True
    assert result["error"]["code"] == "denied"
    assert lite_stdio._tool_success(result)["isError"] is True


def test_configuration_errors_are_reported_instead_of_hidden(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("EVERYTHING_MCP_ALLOWED_ROOTS", "not json")

    result = lite_stdio.call_tool_result("everything_count", {"query": "ext:py", "scope": SCOPE})

    assert result["isError"] is True
    assert "EVERYTHING_MCP_ALLOWED_ROOTS" in result["content"][0]["text"]


def test_empty_results_and_refinement_guidance_are_not_errors() -> None:
    empty = server.everything_search("ext:py", SCOPE, adapter=Adapter(SearchBatch(hits=(), total_count=0)), policy=POLICY)
    broad = server.everything_search("*", None, adapter=Adapter(error=AssertionError()), policy=POLICY)

    assert empty == {"countReturned": 0, "truncated": False, "items": [], "totalCount": 0}
    assert broad["tooBroad"] is True
    assert lite_stdio._tool_success(empty)["isError"] is False
    assert lite_stdio._tool_success(broad)["isError"] is False


def test_search_publishes_everythings_total_count() -> None:
    hits = [SearchHit(rf"{SCOPE}\file{i}.py") for i in range(3)]

    result = server.everything_search(
        "ext:py", SCOPE, limit=2, adapter=Adapter(SearchBatch(hits=hits, total_count=812)), policy=POLICY
    )

    assert result["countReturned"] == 2
    assert result["truncated"] is True
    assert result["totalCount"] == 812


def test_tool_text_is_compact_json() -> None:
    text = lite_stdio._tool_success({"items": ["C:\\한글"], "countReturned": 1})["content"][0]["text"]

    assert text == '{"items":["C:\\\\한글"],"countReturned":1}'


def test_syntax_help_is_answered_by_the_broker_without_a_worker(monkeypatch: MonkeyPatch) -> None:
    def no_worker(*args: object, **kwargs: object) -> object:
        raise AssertionError("syntax help must not spawn a worker")

    monkeypatch.setattr(subprocess, "run", no_worker)

    result = lite_stdio.worker_tool_result("everything_syntax_help", {"topic": "regex"}, 5)

    assert result["isError"] is False
    assert result["content"][0]["text"].startswith("regex:")


def test_invalid_arguments_are_rejected_before_spawning(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")))

    result = lite_stdio.worker_tool_result("everything_search", {"query": "ext:py", "limit": 0}, 5)

    assert result["isError"] is True


def test_worker_command_is_isolated_and_skips_site() -> None:
    command = lite_stdio.worker_command()

    assert command[1:3] == ["-I", "-S"]
    # The package parent can be site-packages; it must not shadow the standard library.
    assert "sys.path.append(sys.argv[1])" in command[4]
    assert "sys.path.insert" not in command[4]
    assert Path(command[-1]) == SRC
    assert Path(command[0]).is_file()


def test_worker_runs_a_real_tool_call_and_exits(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("EVERYTHING_MCP_BACKEND", "es")
    monkeypatch.setenv("EVERYTHING_ES_EXE", str(SRC / "missing-es.exe"))
    monkeypatch.setenv("PATH", "")

    result = lite_stdio.worker_tool_result("everything_status", {}, 30)

    assert result["isError"] is False
    assert result["structuredContent"]["backend"] == "none"


def test_worker_is_killed_after_its_deadline(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    marker = tmp_path / "still-running"
    script = f"import time, pathlib; time.sleep(5); pathlib.Path({str(marker)!r}).write_text('x')"
    monkeypatch.setattr(lite_stdio, "worker_command", lambda: [sys.executable, "-c", script])

    started = time.monotonic()
    result = lite_stdio.worker_tool_result("everything_status", {}, 0.5)

    assert result["isError"] is True
    assert "exceeded 0.5 seconds" in result["content"][0]["text"]
    assert time.monotonic() - started < 4
    time.sleep(5.5)
    assert not marker.exists()


@pytest.mark.parametrize("returncode", [2, 3])
def test_untrustworthy_worker_output_becomes_a_generic_tool_error(monkeypatch: MonkeyPatch, returncode: int) -> None:
    script = f"import sys; sys.stdout.write('{{\"content\":[],\"isError\":false}}'); sys.exit({returncode})"
    monkeypatch.setattr(lite_stdio, "worker_command", lambda: [sys.executable, "-c", script])

    result = lite_stdio.worker_tool_result("everything_status", {}, 10)

    assert result == {"content": [{"type": "text", "text": lite_stdio.TOOL_EXECUTION_ERROR}], "isError": True}


def _lite_env(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONUTF8", "PYTHONIOENCODING"}}
    env.update(PYTHONPATH=str(SRC), PYTHONUTF8="0", **extra)
    return env


def _rpc(*messages: dict[str, Any]) -> bytes:
    return b"".join(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n" for message in messages)


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def test_lite_stdio_speaks_utf8_regardless_of_the_windows_code_page() -> None:
    request = _rpc(
        INITIALIZE,
        INITIALIZED,
        {"jsonrpc": "2.0", "id": "핑-😺", "method": "ping"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "everything_syntax_help", "arguments": {"주제": "x"}},
        },
    )
    completed = subprocess.run(
        [sys.executable, "-m", "everything_mcp.lite_stdio"],
        input=request,
        capture_output=True,
        env=_lite_env(),
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    responses = [json.loads(line) for line in completed.stdout.decode("utf-8").splitlines()]
    assert responses[1] == {"jsonrpc": "2.0", "id": "핑-😺", "result": {}}
    assert responses[2]["result"]["isError"] is True
    assert "주제" in responses[2]["result"]["content"][0]["text"]


def test_lite_stdio_rejects_invalid_utf8_as_a_parse_error() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "everything_mcp.lite_stdio"],
        input=b'{"jsonrpc":"2.0","id":1,"method":"ping","x":"\xff"}\n',
        capture_output=True,
        env=_lite_env(),
        timeout=30,
        check=False,
    )

    assert json.loads(completed.stdout)["error"]["code"] == -32700


def test_lite_stdio_can_exit_when_idle() -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "everything_mcp.lite_stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_lite_env(EVERYTHING_MCP_IDLE_EXIT_SECONDS="1"),
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(_rpc(INITIALIZE))
        process.stdin.flush()
        assert json.loads(process.stdout.readline())["id"] == 1
        assert process.wait(timeout=15) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_broker_does_not_keep_the_backend_loaded_after_a_tool_call() -> None:
    probe = (
        "import sys\n"
        "from everything_mcp import lite_stdio\n"
        "result = lite_stdio.worker_tool_result('everything_status', {}, 30)\n"
        "assert type(result['isError']) is bool, result\n"
        "loaded = sorted(m for m in sys.modules if m in {'ctypes', 'everything_mcp.server'} "
        "or m.startswith('everything_mcp.adapters'))\n"
        "print(loaded)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, env=_lite_env(), timeout=60, check=False
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"
