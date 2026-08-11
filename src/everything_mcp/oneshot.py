"""Execute one existing Everything_Mew tool call, then exit."""

from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO, TextIO, cast

from .lite_stdio import call_tool_result
from .tool_specs import TOOL_SPEC_BY_NAME

INVOCATION_ERROR = "ONESHOT_INVOCATION_ERROR\n"
MAX_REQUEST_BYTES = 65_536


def parse_request(data: bytes) -> tuple[str, dict[str, Any]]:
    raw = json.loads(data.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError
    request = cast(dict[str, Any], raw)
    if set(request) != {"schemaVersion", "tool", "arguments"}:
        raise ValueError
    if type(request["schemaVersion"]) is not int or request["schemaVersion"] != 1:
        raise ValueError
    tool = request["tool"]
    arguments = request["arguments"]
    if not isinstance(tool, str) or tool not in TOOL_SPEC_BY_NAME or not isinstance(arguments, dict):
        raise ValueError
    return tool, cast(dict[str, Any], arguments)


def run(stdin: BinaryIO, stdout: BinaryIO, stderr: TextIO) -> int:
    try:
        data = stdin.read(MAX_REQUEST_BYTES + 1)
        if len(data) > MAX_REQUEST_BYTES:
            raise ValueError
        tool, arguments = parse_request(data)
        result = call_tool_result(tool, arguments)
        stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        stdout.flush()
        return 1 if result["isError"] else 0
    except Exception:
        stderr.write(INVOCATION_ERROR)
        stderr.flush()
        return 2


def main() -> None:
    raise SystemExit(run(sys.stdin.buffer, sys.stdout.buffer, sys.stderr))


if __name__ == "__main__":
    main()
