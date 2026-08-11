from importlib import import_module
import json
from pathlib import Path
import tomllib

contracts = import_module("everything_mcp.contracts")


def test_limit_defaults_and_hard_cap() -> None:
    assert contracts.clamp_limit(None) == contracts.DEFAULT_LIMIT
    assert contracts.clamp_limit(0) == 1
    assert contracts.clamp_limit(500) == contracts.HARD_LIMIT


def test_path_first_default_and_metadata_opt_in() -> None:
    hits = [contracts.SearchHit(path=r"C:\Work\project\README.md", size=123, date_modified="2026-04-26T10:00:00+09:00", attributes="A")]

    assert contracts.path_first_items(hits, metadata=False) == [r"C:\Work\project\README.md"]
    assert contracts.path_first_items(hits, metadata=True) == [
        {
            "path": r"C:\Work\project\README.md",
            "size": 123,
            "dateModified": "2026-04-26T10:00:00+09:00",
            "attributes": "A",
        }
    ]


def test_runtime_entrypoints_and_host_defaults_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]

    opencode = json.loads((root / "opencode.example.json").read_text(encoding="utf-8"))
    assert opencode["mcp"]["everything-mew"]["command"] == ["everything-mew-lite"]
    with (root / "pyproject.toml").open("rb") as pyproject_file:
        scripts = tomllib.load(pyproject_file)["project"]["scripts"]
    assert scripts == {
        "everything-mew": "everything_mcp.__main__:main",
        "everything-mcp": "everything_mcp.__main__:main",
        "everything-mew-lite": "everything_mcp.lite_stdio:main",
        "everything-mcp-lite": "everything_mcp.lite_stdio:main",
        "everything-mew-once": "everything_mcp.oneshot:main",
        "everything-mcp-once": "everything_mcp.oneshot:main",
    }

    for relative_path in ("README.md", "README.ko.md"):
        readme = (root / relative_path).read_text(encoding="utf-8")
        assert "Everything_Mew" in readme
        assert "everything-mew-once" in readme
        assert "schemaVersion" in readme
        assert "enabled = false" in readme
        assert '"command": ["everything-mew-lite"]' in readme

    english_readme = (root / "README.md").read_text(encoding="utf-8")
    assert 'py -m pip install -e ".[server]"' in english_readme
    assert "Contributor notes" in english_readme
    assert "trusted local Everything binaries" in english_readme


def test_final_release_metadata_and_notes_are_consistent() -> None:
    root = Path(__file__).resolve().parents[1]

    with (root / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    assert pyproject["project"]["version"] == "0.2.0"
    sdist_includes = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert "/.gitattributes" in sdist_includes
    assert "/docs/releases/*.md" in sdist_includes

    attributes = (root / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto eol=lf" in attributes

    expected_release_references = {
        "README.md": ("v0.1.0", "v0.2.0"),
        "README.ko.md": ("v0.1.0", "v0.2.0"),
        "CHANGELOG.md": ("[0.1.0]", "[0.2.0]"),
        "SECURITY.md": ("`0.2.x`", "Current supported / 현재 지원"),
    }
    for relative_path, references in expected_release_references.items():
        document = (root / relative_path).read_text(encoding="utf-8")
        for reference in references:
            assert reference in document

    for release_note in ("v0.1.0.md", "v0.2.0-rc.1.md", "v0.2.0.md"):
        assert (root / "docs" / "releases" / release_note).is_file()


def test_repository_skill_is_codex_named_and_matches_supported_syntax_profile() -> None:
    root = Path(__file__).resolve().parents[1]
    skill = (root / "skills" / "everything" / "SKILL.md").read_text(encoding="utf-8")

    assert "name: everything-mew" in skill
    assert "compatibility: opencode" not in skill
    assert "Everything 1.4.1" in skill
    assert '"C:\\Work\\project\\"' in skill
    assert 'regex:"gr(a|e)y"' in skill
    assert "from-disk:" in skill
    assert "content*:" in skill
    assert '$runner = "C:\\replace\\with\\absolute\\path\\to\\everything-mew-once.exe"' in skill
    assert "Get-Command everything-mew-once" not in skill
    assert "schemaVersion = 1" in skill
    assert "ConvertTo-Json -Compress -Depth 4" in skill
    assert "$LASTEXITCODE -notin 0, 1" in skill
    assert "Invoke-Expression" not in skill


def test_release_documents_cover_scope_count_and_syntax_hardening() -> None:
    root = Path(__file__).resolve().parents[1]
    documents = (
        root / "README.md",
        root / "README.ko.md",
        root / "CHANGELOG.md",
        root / "docs" / "releases" / "v0.2.0.md",
        root / "docs" / "SDK_DESIGN_REVIEW.md",
    )

    for path in documents:
        content = path.read_text(encoding="utf-8")
        assert "Everything 1.4.1" in content, path
        assert "SetMax(0)" in content, path
        assert "from-disk:" in content, path
        assert "content*:" in content, path
        assert '"C:\\Work\\project\\"' in content, path
