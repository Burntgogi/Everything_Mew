from everything_mcp.adapters import sdk_ipc


def test_sdk_sort_flags_map_all_public_sorts_to_ascending_constants() -> None:
    assert sdk_ipc.SORT_FLAGS == {
        "name": sdk_ipc.EVERYTHING_SORT_NAME_ASCENDING,
        "path": sdk_ipc.EVERYTHING_SORT_PATH_ASCENDING,
        "size": sdk_ipc.EVERYTHING_SORT_SIZE_ASCENDING,
        "date_modified": sdk_ipc.EVERYTHING_SORT_DATE_MODIFIED_ASCENDING,
    }
    assert sdk_ipc.SORT_FLAGS["date_modified"] == 13
