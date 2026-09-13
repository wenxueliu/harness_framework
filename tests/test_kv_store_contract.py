"""Backend-neutral KVStore listing contract tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from harness_framework.file_store import FileStore
from harness_framework.local_store import LocalStore


@pytest.fixture(params=["local", "file"])
def store(request, tmp_path: Path):
    if request.param == "local":
        return LocalStore()
    return FileStore(str(tmp_path / "kv.json"))


def test_kv_list_is_sorted_filtered_and_paginated(store):
    store.kv_put("other/ignored", "x")
    store.kv_put("groups/c", "three")
    store.kv_put("groups/a", "one")
    store.kv_put("groups/b", "two")

    first, cursor = store.kv_list("groups/", limit=2)
    second, end = store.kv_list("groups/", cursor=cursor, limit=2)

    assert [(item["key"], item["value"]) for item in first] == [
        ("groups/a", "one"),
        ("groups/b", "two"),
    ]
    assert [item["key"] for item in second] == ["groups/c"]
    assert cursor is not None
    assert end is None
    assert all(item["modify_index"] > 0 for item in first + second)


@pytest.mark.parametrize("limit", [0, -1, 1001, True, "10"])
def test_kv_list_rejects_invalid_limit(store, limit):
    with pytest.raises(ValueError, match="limit"):
        store.kv_list("groups/", limit=limit)


def test_kv_list_rejects_invalid_cursor(store):
    with pytest.raises(ValueError, match="cursor"):
        store.kv_list("groups/", cursor="!not-base64!", limit=10)


def test_kv_list_cursor_cannot_be_reused_for_another_prefix(store):
    store.kv_put("groups/a", "one")
    store.kv_put("groups/b", "two")
    _, cursor = store.kv_list("groups/", limit=1)
    with pytest.raises(ValueError, match="cursor"):
        store.kv_list("events/", cursor=cursor, limit=1)


def test_delete_advances_index_and_blocking_read_observes_absence(store):
    store.kv_put("groups/a", "one")
    _, index = store.kv_get("groups/a")
    store.kv_delete("groups/a")

    value, new_index = store.kv_blocking_get("groups/a", index=index, wait="0s")

    assert value is None
    assert new_index > index


def test_consul_client_kv_list_normalizes_recursive_response(monkeypatch):
    from harness_framework.consul_client import ConsulClient

    client = ConsulClient(addr="127.0.0.1:8500")
    monkeypatch.setattr(client, "kv_get", lambda prefix, recurse: ([
        {"Key": "groups/b", "_decoded": "two", "ModifyIndex": 12},
        {"Key": "groups/a", "_decoded": "one", "ModifyIndex": 11},
    ], 12))

    page, cursor = client.kv_list("groups/", limit=10)

    assert page == [
        {"key": "groups/a", "value": "one", "modify_index": 11},
        {"key": "groups/b", "value": "two", "modify_index": 12},
    ]
    assert cursor is None
