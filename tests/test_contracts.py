from importlib import import_module
from pathlib import Path

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


def test_readme_and_opencode_prefer_release_console_entrypoint() -> None:
    root = Path(__file__).resolve().parents[1]

    assert '"everything-mew"' in (root / "opencode.example.json").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Everything_Mew" in readme
    assert 'py -m pip install -e ".[server]"' in readme
    assert "Trusted local paths" in readme
