from everything_mcp.adapters import sdk_ipc


def test_sdk_sort_flags_map_all_public_sorts_to_ascending_constants() -> None:
    assert sdk_ipc.SORT_FLAGS == {"name": 1, "path": 3, "size": 5, "date_modified": 13}
