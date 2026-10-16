"""人工消息的存储布局：作业流实例 vs 旧 workflow。

作业流实例的任务挂在 `jobflows/instances/<id>/tasks/` 下，旧 workflow 挂在
`workflows/<req_id>/tasks/` 下。写入和读取必须落在同一处，否则「发送消息没有
反应」——消息写进一个没人读的前缀。
"""
from __future__ import annotations

import json

from harness_framework.human_interaction import (
    claim_next_human_message,
    create_human_message,
    finish_human_message,
    has_pending_interrupt,
    list_human_messages,
)
from tests.conftest import MockConsulStore


def _instance_store(instance_id: str = "inst-1") -> MockConsulStore:
    store = MockConsulStore()
    store._store[f"jobflows/instances/{instance_id}/meta"] = json.dumps({
        "instance_id": instance_id, "template_id": "tpl-1",
    })
    store._store[f"jobflows/instances/{instance_id}/tasks/dev/status"] = json.dumps({
        "task_id": "dev", "state": "PENDING", "attempt_count": 0,
    })
    return store


def _workflow_store(req_id: str = "req-1") -> MockConsulStore:
    store = MockConsulStore()
    store._store[f"workflows/{req_id}/tasks/build/status"] = "PENDING"
    return store


class TestJobFlowLayout:
    def test_message_is_written_under_the_instance_prefix(self):
        store = _instance_store()
        item = create_human_message(
            store, "inst-1", "dev", message="hi", actor="alice",
        )
        key = f"jobflows/instances/inst-1/tasks/dev/human/messages/{item['message_id']}"
        assert key in store._store
        assert not any(k.startswith("workflows/") for k in store._store)

    def test_list_reads_back_the_same_messages(self):
        store = _instance_store()
        create_human_message(store, "inst-1", "dev", message="hi", actor="alice")
        create_human_message(
            store, "inst-1", "dev", message="停下", actor="bob", mode="interrupt",
        )
        messages = list_human_messages(store, "inst-1", "dev")
        assert [item["message"] for item in messages] == ["hi", "停下"]
        assert all(item["status"] == "PENDING" for item in messages)

    def test_interrupt_and_claim_lifecycle(self):
        store = _instance_store()
        create_human_message(
            store, "inst-1", "dev", message="停下", actor="bob", mode="interrupt",
        )
        assert has_pending_interrupt(store, "inst-1", "dev") is True

        claimed = claim_next_human_message(store, "inst-1", "dev")
        assert claimed is not None and claimed["status"] == "PROCESSING"
        assert list_human_messages(store, "inst-1", "dev", pending_only=True) == []

        finished = finish_human_message(
            store, "inst-1", "dev", claimed, status="APPLIED", response="done",
        )
        assert finished["status"] == "APPLIED"
        assert has_pending_interrupt(store, "inst-1", "dev") is False
        assert list_human_messages(store, "inst-1", "dev")[0]["response"] == "done"

    def test_message_for_unknown_task_still_uses_instance_prefix(self):
        """实例存在但任务不存在时，布局判定仍然跟随实例，不落到 workflows/。"""
        store = _instance_store()
        item = create_human_message(
            store, "inst-1", "missing", message="hi", actor="alice",
        )
        assert (f"jobflows/instances/inst-1/tasks/missing/human/messages/"
                f"{item['message_id']}") in store._store


class TestWorkflowLayout:
    def test_legacy_workflow_keeps_using_the_workflows_prefix(self):
        store = _workflow_store()
        item = create_human_message(
            store, "req-1", "build", message="hi", actor="alice",
        )
        assert f"workflows/req-1/tasks/build/human/messages/{item['message_id']}" in store._store
        assert not any(k.startswith("jobflows/") for k in store._store)
        assert [m["message"] for m in list_human_messages(store, "req-1", "build")] == ["hi"]

    def test_layouts_do_not_leak_between_scopes(self):
        """同名 req/task 的实例与 workflow 各自读写自己的消息。"""
        store = _workflow_store()
        store._store["jobflows/instances/req-1/meta"] = json.dumps({"instance_id": "req-1"})
        item = create_human_message(
            store, "req-1", "build", message="hi", actor="alice",
        )
        # 同名实例存在时按实例布局写入
        assert (f"jobflows/instances/req-1/tasks/build/human/messages/"
                f"{item['message_id']}") in store._store
        assert list_human_messages(store, "req-1", "build") == [item]
