from importlib import import_module
import subprocess
from pathlib import Path
from typing import Any, TypedDict

import pytest
from pytest import MonkeyPatch

es_cli = import_module("everything_mcp.adapters.es_cli")
sdk_ipc = import_module("everything_mcp.adapters.sdk_ipc")
config_module = import_module("everything_mcp.config")
errors = import_module("everything_mcp.errors")
query_module = import_module("everything_mcp.query")
server = import_module("everything_mcp.server")
SearchHit = import_module("everything_mcp.contracts").SearchHit


@pytest.fixture(autouse=True)
def unrestricted_policy_for_query_guard_tests(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("EVERYTHING_MCP_ALLOWED_ROOTS", raising=False)
    monkeypatch.setenv("EVERYTHING_MCP_ALLOW_UNSCOPED", "1")


class SubprocessRunCall(TypedDict):
    args: list[str]
    check: bool
    capture_output: bool
    text: bool
    shell: bool
    timeout: float


class SafetyAdapter:
    name = "fake"

    def __init__(self) -> None:
        self.count_calls: list[tuple[str, str | None]] = []
        self.search_calls: list[tuple[str, str | None]] = []

    def count(self, query: str, scope: str | None = None) -> int:
        self.count_calls.append((query, scope))
        return 1

    def search(self, query: str, scope: str | None = None, **_: Any) -> list[object]:
        self.search_calls.append((query, scope))
        return []


SAFETY_MATRIX = (
    ("single exclusion", "!node_modules", None, True),
    ("multiple exclusions", "!node_modules !.git", None, True),
    ("quoted exclusion", '!"node modules"', None, True),
    ("quoted exclusion scope", '!"node modules"', r"C:\Work\project", True),
    ("mixed quoted and unquoted exclusions", '!"node modules" !.git', None, True),
    ("extension with trailing exclusion", "ext:md !node_modules", None, True),
    ("extension with leading exclusion", "!node_modules ext:md", None, True),
    ("content with excluded extension text", 'content:foo !"ignored ext:md"', r"C:\Work\project", True),
    (
        "content with excluded size and date text",
        'content:foo !"ignored size:100 dm:2024-01-01"',
        r"C:\Work\project",
        True,
    ),
    ("drive root", "C:\\", None, True),
    ("quoted drive root", r'"C:\"', None, True),
    ("slash-normalized drive root", "C:/", None, True),
    ("path drive root", "path:C:\\", None, True),
    ("quoted path drive root", r'path:"C:\"', None, True),
    ("drive root scope", "README.md", "C:\\", True),
    ("UNC server root", r"\\server", None, True),
    ("UNC share root", r"\\server\share", None, True),
    ("quoted UNC share root", r'"\\server\share"', None, True),
    ("path UNC share root", r"path:\\server\share", None, True),
    ("quoted path UNC share root", r'path:"\\server\share"', None, True),
    ("UNC share root scope", "README.md", r"\\server\share", True),
    ("relative scope", "README.md", r"Work\project", True),
    ("drive-relative scope", "README.md", r"C:Work\project", True),
    ("local project filename", "README.md", r"C:\\Work\\project", False),
    ("UNC project filter", "ext:md", r"\\server\share\project", False),
    ("unscoped positive filter", "report ext:md", None, False),
    ("filename, extension, and exclusion", "README.md ext:md !node_modules", None, False),
    ("project extension and exclusion", "ext:md !node_modules", r"C:\\Work\\project", False),
    ("content real extension and exclusion", 'content:foo ext:md !"ignored ext:md"', r"C:\\Work\\project", False),
    ("OR date filter with universal branch", "dm:today | *", None, True),
    ("OR size filter with universal branch", "size:>0|*", None, True),
    ("OR path filter with universal branch", r"path:C:\Work|*", None, True),
    ("scoped filename with universal OR branch", "foo | *", r"C:\Work\project", True),
    ("regex-only expression", "regex:.*", None, True),
    ("quoted regex-only expression", 'regex:"^.*$"', None, True),
    ("one-or-more regex-only expression", "regex:.+", None, True),
    ("anchored regex-only expression", "regex:^.+$", None, True),
    ("adversarial leaf regex", r"regex:^[^\\/]+$", None, True),
    ("scoped adversarial leaf regex", r"regex:^[^\\/]+$", r"C:\Work\project", True),
    ("nested path regex-only expression", "regex:path:.*", None, True),
    ("nested modifier adversarial regex", r"case:regex:path:^[^\\/]+$", r"C:\Work\project", True),
    ("nested function regex-only expression", "regex:size:.+", None, True),
    ("nested path universal wildcard", "wildcards:path:**", None, True),
    ("nested modifiers universal wildcard", "case:wildcards:path:***", None, True),
    ("specific regex still needs indexed filter", r"regex:^README.*\.md$", r"C:\Work\project", True),
    ("extension plus specific regex", r"ext:md regex:^README.*\.md$", None, False),
    ("size comparison plus adversarial regex", r"size:>1mb regex:^[^\\/]+$", None, False),
    ("path filter plus adversarial regex", r"path:C:\Work regex:^[^\\/]+$", None, False),
    ("plain filename does not narrow regex", r"README.md regex:^README", r"C:\Work\project", True),
    ("wildcard modifier does not narrow regex", r"regex:^[^\\/]+$ wildcards:*.py", None, True),
    (
        "nested wildcard modifier does not narrow regex",
        r"regex:^test case:wildcards:path:C:\Work\*.py",
        None,
        True,
    ),
    ("legitimate scoped wildcard", r"wildcards:path:C:\Work\*.md", None, False),
    ("greater-than size comparison", "size:>1mb", None, False),
    ("greater-than-or-equal date comparison", "dm:>=2025-01-01", None, False),
    ("less-than size comparison", "size:<1mb", None, False),
    ("comparisons inside grouping", "<size:>1mb|size:<1mb> report", None, False),
    ("grouped universal OR branch", "<report|*> ext:md", None, True),
    ("grouped narrowing alternatives", "<report|summary> ext:md", None, False),
    ("OR alternatives share filename constraint", "report ext:md|ext:txt", None, False),
    ("negated group with positive filter", "ext:md !<node_modules|.git>", r"C:\Work\project", False),
    ("negated AND group expands broadly", "!<foo bar>", r"C:\Work\project", True),
    ("malformed unclosed group", "<report|summary ext:md", None, True),
    ("malformed empty OR branch", "report||summary ext:md", None, True),
    ("malformed unclosed quote", 'path:"C:\\Work report', None, True),
)


@pytest.mark.parametrize(("_name", "query", "scope", "expected_broad"), SAFETY_MATRIX)
def test_broad_query_safety_matrix(_name: str, query: str, scope: str | None, expected_broad: bool) -> None:
    assert server.is_broad_query(query, scope) is expected_broad


@pytest.mark.parametrize("query", ('!"node modules"', '!"node modules" !.git'))
def test_exclusion_only_query_recognizes_quoted_phrases(query: str) -> None:
    assert query_module.is_exclusion_only_query(query) is True


@pytest.mark.parametrize(
    ("query", "expected"),
    (
        ('content:foo !"ignored ext:md"', False),
        ('content:foo !"ignored size:100 dm:2024-01-01"', False),
        ('content:foo "ignored ext:md"', False),
        ('content:foo EXT:md !"ignored size:100"', True),
    ),
)
def test_positive_narrowing_filter_uses_only_real_positive_filter_terms(query: str, expected: bool) -> None:
    assert query_module.has_positive_narrowing_filter(query) is expected


def test_regex_is_not_reported_as_a_positive_indexed_filter() -> None:
    assert query_module.has_positive_narrowing_filter(r"regex:^test") is False
    assert query_module.has_positive_narrowing_filter(r"ext:py regex:^test") is True


@pytest.mark.parametrize(
    ("scope", "expected"),
    (
        (r"C:\Work", '"C:\\Work\\" <ext:md>'),
        ("C:\\Program Files\\", '"C:\\Program Files\\" <ext:md>'),
        ("C:/Work/project/", '"C:\\Work\\project\\" <ext:md>'),
        (r"\\server\share\project", '"\\\\server\\share\\project\\" <ext:md>'),
    ),
)
def test_scope_composition_uses_an_exact_recursive_folder_boundary(scope: str, expected: str) -> None:
    assert query_module.compose_query("ext:md", scope) == expected


@pytest.mark.parametrize(
    ("path", "scope", "expected"),
    (
        (r"C:\Work\project\README.md", r"C:\Work\project", True),
        (r"c:\work\PROJECT\src\main.py", r"C:\Work\project", True),
        (r"C:\Work\project-backup\README.md", r"C:\Work\project", False),
        (r"\\server\share\project\README.md", r"\\server\share\project", True),
        (r"\\server\share\project-old\README.md", r"\\server\share\project", False),
    ),
)
def test_path_scope_boundary_is_case_insensitive_and_rejects_prefix_siblings(
    path: str, scope: str, expected: bool
) -> None:
    assert query_module.is_path_within_scope(path, scope) is expected


def test_search_discards_adapter_hits_outside_scope_before_serializing() -> None:
    class PrefixSiblingAdapter(SafetyAdapter):
        def search(self, query: str, scope: str | None = None, **_: Any) -> list[object]:
            self.search_calls.append((query, scope))
            return [
                SearchHit(path=r"C:\Users\name\.config\opencode-backups\Everything64.dll"),
                SearchHit(path=r"C:\Users\name\.config\opencode\Everything64.dll"),
            ]

    result = server.everything_search(
        "Everything64.dll ext:dll",
        scope=r"C:\Users\name\.config\opencode",
        adapter=PrefixSiblingAdapter(),
    )

    assert result["items"] == [r"C:\Users\name\.config\opencode\Everything64.dll"]


@pytest.mark.parametrize(
    "content_function",
    (
        "content",
        "ansicontent",
        "ansi-content",
        "contenta",
        "ascii-content",
        "binary-content",
        "byte-stream-content",
        "octet-stream-content",
        "utf8content",
        "utf-8-content",
        "utf16content",
        "utf-16-content",
        "utf16becontent",
        "utf-16be-content",
        "alternate-data-stream-ansi",
        "ads-ansi",
        "alternate-data-stream-hex",
        "ads-hex",
        "alternate-data-stream-text-plain",
        "ads-text-plain",
        "alternate-data-stream-utf16",
        "ads-utf16",
        "alternate-data-stream-utf16be",
        "ads-utf16be",
        "alternate-data-stream-utf8",
        "ads-utf8",
    ),
)
def test_every_content_alias_requires_non_root_scope_and_filter(content_function: str) -> None:
    query = f"{content_function}:needle"

    assert server.is_broad_query(query, scope=r"C:\Work\project") is True
    assert server.is_broad_query(f"{query} ext:md", scope=r"C:\Work\project") is False
    assert server.is_broad_query(f"{query} ext:md", scope="C:\\") is True


@pytest.mark.parametrize(
    "query",
    (
        "content*:needle",
        r"binary:content:\x00",
        "hex:content:4142",
        "each-line:content:needle",
        'dot-all:regex:content:"^a.*b$"',
        "wildcards:content*:needle*",
    ),
)
def test_nested_and_literal_content_forms_cannot_bypass_slow_io_policy(query: str) -> None:
    assert server.is_broad_query(query, scope=r"C:\Work\project") is True
    assert server.is_broad_query(f"{query} ext:txt", scope=r"C:\Work\project") is False


def test_literal_regex_modifier_still_requires_a_separate_indexed_filter() -> None:
    assert server.is_broad_query("regex*:needle", scope=r"C:\Work\project") is True
    assert server.is_broad_query("regex*:needle ext:txt", scope=r"C:\Work\project") is False


@pytest.mark.parametrize(
    "modifier",
    ("from-disk", "fromdisk", "?from-disk", "no-highlight:from-disk"),
)
def test_forced_disk_modifier_requires_non_root_scope_and_separate_filter(modifier: str) -> None:
    query = f"{modifier}:length:>5m"

    assert server.is_broad_query(query, scope=r"C:\Work\project") is True
    assert server.is_broad_query(f"{query} ext:md", scope=r"C:\Work\project") is False
    assert server.is_broad_query(f"{query} ext:md", scope="C:\\") is True


def test_function_chain_detection_does_not_treat_a_content_folder_as_content_search() -> None:
    assert server.is_broad_query(r"path:C:\content:folder") is False


def test_slow_io_policy_is_enforced_per_or_alternative() -> None:
    assert server.is_broad_query(
        "<ansi-content:needle>|<report ext:md>",
        scope=r"C:\Work\project",
    ) is True
    assert server.is_broad_query(
        "<ansi-content:needle ext:txt>|<from-disk:length:>5m ext:md>",
        scope=r"C:\Work\project",
    ) is False


def test_content_policy_is_enforced_per_or_alternative() -> None:
    assert server.is_broad_query(
        "<utf8content:needle>|<report ext:md>",
        scope=r"C:\Work\project",
    ) is True
    assert server.is_broad_query(
        "<utf8content:needle ext:txt>|<report ext:md>",
        scope=r"C:\Work\project",
    ) is False


def test_regex_requires_a_separate_indexed_filter_in_every_or_branch() -> None:
    assert server.is_broad_query(
        r"<ext:py regex:^test>|<ext:txt regex:^document>",
        scope=r"C:\Work\project",
    ) is False
    assert server.is_broad_query(
        r"<ext:py regex:^test>|regex:^document",
        scope=r"C:\Work\project",
    ) is True


def test_content_and_regex_require_a_separate_indexed_filter() -> None:
    assert server.is_broad_query(
        r"content:needle regex:^[^\\/]+$",
        scope=r"C:\Work\project",
    ) is True
    assert server.is_broad_query(
        r"content:needle regex:^[^\\/]+$ ext:txt",
        scope=r"C:\Work\project",
    ) is False


@pytest.mark.parametrize(
    "universal_filter",
    ("regex:.+", "regex:path:.*", "regex:size:.+", "wildcards:path:**", "case:regex:path:^.+$"),
)
def test_pattern_modifiers_do_not_replace_an_indexed_content_filter(universal_filter: str) -> None:
    assert server.is_broad_query(
        f"content:needle {universal_filter}",
        scope=r"C:\Work\project",
    ) is True


@pytest.mark.parametrize(
    ("_name", "query", "scope", "expected_broad"),
    tuple(case for case in SAFETY_MATRIX if case[3]),
)
def test_rejected_safety_matrix_cases_do_not_call_adapters(
    _name: str, query: str, scope: str | None, expected_broad: bool
) -> None:
    adapter = SafetyAdapter()

    count = server.everything_count(query, scope=scope, adapter=adapter)
    search = server.everything_search(query, scope=scope, adapter=adapter)

    assert expected_broad is True
    assert count["tooBroad"] is True
    assert search["tooBroad"] is True
    assert adapter.count_calls == []
    assert adapter.search_calls == []
    assert "exclusion" not in count["recommendation"]
    assert "exclusion" not in search["recommendation"]


def test_sdk_adapter_missing_dll_reports_status_without_crashing(tmp_path: Path) -> None:
    adapter = sdk_ipc.SdkIpcAdapter(config_module.EverythingConfig(everything_exe=tmp_path / "Everything.exe", sdk_dll=tmp_path / "missing.dll"))

    status = adapter.status()

    assert status.backend == "none"
    assert status.everything_running is False
    assert any("DLL" in note for note in status.notes)


def test_es_cli_uses_subprocess_without_shell(monkeypatch: MonkeyPatch) -> None:
    calls: list[SubprocessRunCall] = []

    def fake_run(
        args: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
        shell: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "check": check, "capture_output": capture_output, "text": text, "shell": shell, "timeout": timeout})
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="3\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))

    assert adapter.count("ext:md", scope=r"C:\Work") == 3
    assert calls[0]["shell"] is False
    assert calls[0]["timeout"] == es_cli.DEFAULT_ES_TIMEOUT_SECONDS
    assert calls[0]["args"][0] == r"C:\Tools\es.exe"
    assert calls[0]["args"][-1] == '"C:\\Work\\" <ext:md>'


def test_es_cli_bad_count_raises_actionable_query_error(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args, returncode=0, stdout="not-a-number\n", stderr=""),
    )
    adapter = es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))

    try:
        adapter.count("ext:md", scope=r"C:\Work")
    except errors.QueryError as exc:
        assert "numeric count" in str(exc)
    else:
        raise AssertionError("Expected QueryError for non-numeric ES count output")


def test_es_cli_rejects_leading_dash_query() -> None:
    adapter = es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))

    try:
        adapter.count("-dangerous-option")
    except errors.QueryError as exc:
        assert "must not start with '-'" in str(exc)
    else:
        raise AssertionError("Expected QueryError for leading-dash ES query")


def test_es_cli_rejects_unknown_sort() -> None:
    try:
        es_cli._sort_arg("unknown")
    except errors.QueryError as exc:
        assert "Unsupported ES CLI sort" in str(exc)
    else:
        raise AssertionError("Expected QueryError for unsupported ES sort")


def test_es_cli_timeout_raises_query_error(monkeypatch: MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="es.exe", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))

    try:
        adapter.count("ext:md", scope=r"C:\Work")
    except errors.QueryError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("Expected QueryError for ES timeout")


def test_es_cli_spaced_scope_is_quoted() -> None:
    adapter = es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))

    assert adapter._compose_query("ext:exe", r"C:\Program Files") == '"C:\\Program Files\\" <ext:exe>'


def test_sdk_adapter_configures_ctypes_signatures() -> None:
    class FakeFunction:
        def __init__(self) -> None:
            self.argtypes = None
            self.restype = None

    class FakeDll:
        def __init__(self) -> None:
            self.Everything_IsDBLoaded = FakeFunction()
            self.Everything_GetMajorVersion = FakeFunction()
            self.Everything_GetMinorVersion = FakeFunction()
            self.Everything_GetRevision = FakeFunction()
            self.Everything_GetBuildNumber = FakeFunction()
            self.Everything_GetTargetMachine = FakeFunction()
            self.Everything_GetResultListSort = FakeFunction()
            self.Everything_GetResultListRequestFlags = FakeFunction()
            self.Everything_Reset = FakeFunction()
            self.Everything_SetSearchW = FakeFunction()
            self.Everything_SetRequestFlags = FakeFunction()
            self.Everything_SetSort = FakeFunction()
            self.Everything_SetMax = FakeFunction()
            self.Everything_SetReplyWindow = FakeFunction()
            self.Everything_SetReplyID = FakeFunction()
            self.Everything_QueryW = FakeFunction()
            self.Everything_IsQueryReply = FakeFunction()
            self.Everything_GetTotResults = FakeFunction()
            self.Everything_GetNumResults = FakeFunction()
            self.Everything_GetLastError = FakeFunction()
            self.Everything_GetResultFullPathNameW = FakeFunction()
            self.Everything_GetResultSize = FakeFunction()
            self.Everything_GetResultDateModified = FakeFunction()
            self.Everything_GetResultAttributes = FakeFunction()

    adapter = sdk_ipc.SdkIpcAdapter.__new__(sdk_ipc.SdkIpcAdapter)
    adapter._dll = FakeDll()

    adapter._configure_functions()

    assert adapter._dll.Everything_QueryW.argtypes is not None
    assert adapter._dll.Everything_GetResultFullPathNameW.restype is not None
    assert adapter._dll.Everything_SetSort.argtypes is not None


def test_sdk_date_modified_ignores_unknown_and_out_of_range_filetime() -> None:
    class FakeDll:
        def __init__(self, raw_value: int) -> None:
            self.raw_value = raw_value

        def Everything_GetResultDateModified(self, index: int, value_pointer: Any) -> bool:
            value_pointer._obj.dwLowDateTime = self.raw_value & 0xFFFFFFFF
            value_pointer._obj.dwHighDateTime = (self.raw_value >> 32) & 0xFFFFFFFF
            return True

    adapter = sdk_ipc.SdkIpcAdapter.__new__(sdk_ipc.SdkIpcAdapter)
    adapter._dll = FakeDll(0)
    assert adapter._result_date_modified(0) is None

    adapter._dll = FakeDll(0xFFFFFFFFFFFFFFFF)
    assert adapter._result_date_modified(0) is None

    adapter._dll = FakeDll(0x7FFFFFFFFFFFFFFF)
    assert adapter._result_date_modified(0) is None
