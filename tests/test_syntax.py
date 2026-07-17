from collections.abc import Callable
from importlib import import_module

server = import_module("everything_mcp.server")


def test_all_four_read_only_tools_are_declared() -> None:
    assert set(server.TOOL_NAMES) == {"everything_status", "everything_count", "everything_search", "everything_syntax_help"}
    for name in server.TOOL_NAMES:
        assert server.TOOL_ANNOTATIONS[name] == {"readOnlyHint": True, "destructiveHint": False}


def test_syntax_help_topic_and_default() -> None:
    assert "ext:md" in server.everything_syntax_help("extension")
    default = server.everything_syntax_help()
    assert "Everything 1.4.1" in default
    assert '"C:\\Work\\project\\"' in default
    assert "!node_modules" in default


def test_syntax_help_is_version_aware_and_explains_operators_and_slow_io() -> None:
    compatibility = server.everything_syntax_help("compatibility")
    operators = server.everything_syntax_help("operators")
    regex = server.everything_syntax_help("regex")
    content = server.everything_syntax_help("content")

    assert "Everything 1.4.1" in compatibility
    assert "Everything 1.5" in compatibility
    assert "OR" in operators and "higher precedence" in operators
    assert 'regex:"gr(a|e)y"' in regex
    assert "quote" in regex.lower()
    assert "from-disk:" in content
    assert "content*:" in content
    assert "nested" in content.lower()
    assert "separate indexed" in content


def test_mcp_wrappers_do_not_expose_adapter_parameter() -> None:
    assert "adapter" not in server._mcp_everything_count.__annotations__
    assert "adapter" not in server._mcp_everything_search.__annotations__


def test_register_tool_does_not_fallback_to_internal_function_name() -> None:
    calls: list[dict[str, object]] = []

    class FakeMcp:
        def tool(self, **kwargs: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
            calls.append(kwargs)
            if "annotations" in kwargs:
                raise TypeError("old FastMCP without annotations")

            def decorator(func: Callable[..., object]) -> Callable[..., object]:
                return func

            return decorator

    server._register_tool(FakeMcp(), server._mcp_everything_status, "everything_status")

    assert calls == [
        {"name": "everything_status", "annotations": {"readOnlyHint": True, "destructiveHint": False}},
        {"name": "everything_status"},
    ]
