from importlib import import_module

server = import_module("everything_mcp.server")


def test_all_four_read_only_tools_are_declared() -> None:
    assert set(server.TOOL_NAMES) == {"everything_status", "everything_count", "everything_search", "everything_syntax_help"}
    for name in server.TOOL_NAMES:
        assert server.TOOL_ANNOTATIONS[name] == {"readOnlyHint": True, "destructiveHint": False}


def test_syntax_help_topic_and_default() -> None:
    assert "ext:md" in server.everything_syntax_help("extension")
    default = server.everything_syntax_help()
    assert "path:" in default
    assert "!node_modules" in default


def test_mcp_wrappers_do_not_expose_adapter_parameter() -> None:
    assert "adapter" not in server._mcp_everything_count.__annotations__
    assert "adapter" not in server._mcp_everything_search.__annotations__


def test_register_tool_does_not_fallback_to_internal_function_name() -> None:
    calls = []

    class FakeMcp:
        def tool(self, **kwargs):
            calls.append(kwargs)
            if "annotations" in kwargs:
                raise TypeError("old FastMCP without annotations")

            def decorator(func):
                return func

            return decorator

    server._register_tool(FakeMcp(), server._mcp_everything_status, "everything_status")

    assert calls == [
        {"name": "everything_status", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
        {"name": "everything_status"},
    ]
