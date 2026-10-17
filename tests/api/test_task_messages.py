"""HTTP 契约：任务人工消息端点同时支持作业流实例与旧 workflow。

回归背景：`/api/workflow/<req>/task/<task>/messages` 过去只认
`workflows/<req>/tasks/<task>/status`，作业流实例（t2 的 dev 任务）发送消息
必然 404，前端表现为「点击发送没有反应」。
"""
from __future__ import annotations

import http.client
import json
import threading

from harness_framework.auth import AuthConfig
from harness_framework.webapi import serve
from tests.conftest import MockConsulStore


def request(port: int, method: str, path: str, body: dict | None = None,
            headers: dict | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(
        method, path,
        body=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    response = connection.getresponse()
    payload = json.loads(response.read() or b"{}")
    connection.close()
    return response.status, payload


def _serve():
    store = MockConsulStore()
    server = serve(
        store, host="127.0.0.1", port=0,
        auth_config=AuthConfig(mode="local", local_user="task-msg-test"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return store, server, server.server_address[1]


def _create_instance(port: int) -> str:
    status, created = request(port, "POST", "/api/templates", {
        "template_id": "msg-template", "name": "Msg",
        "tasks": {"dev": {"type": "backend", "depends_on": []}},
    })
    assert status == 201, created
    status, _ = request(port, "PATCH", "/api/templates/msg-template/draft", {
        "expected_revision": 1,
        "tasks": [{"id": "dev", "type": "backend", "depends_on": []}],
    })
    assert status == 200
    status, published = request(
        port, "POST", "/api/templates/msg-template/publish", {},
        {"Idempotency-Key": "publish-msg-v1"},
    )
    assert status == 200, published
    status, instance = request(port, "POST", "/api/instances", {
        "template_id": "msg-template",
        "version_id": published["version"]["version_id"],
        "name": "msg-main",
        "parameters": {},
    }, {"Idempotency-Key": "instance-msg"})
    assert status == 201, instance
    return instance["instance"]["instance_id"]


def _seed_legacy_workflow(store: MockConsulStore, req_id: str = "req-legacy") -> None:
    store._store[f"workflows/{req_id}/tasks/build/status"] = "IN_PROGRESS"
    store._store[f"workflows/{req_id}/dependencies"] = json.dumps({
        "build": {"type": "backend", "depends_on": []},
    })


def test_message_to_instance_task_is_stored_and_listed():
    store, server, port = _serve()
    try:
        instance_id = _create_instance(port)
        path = f"/api/workflow/{instance_id}/task/dev/messages"

        status, sent = request(port, "POST", path, {"message": "hi"})
        assert status == 202, sent
        assert sent["message"]["message"] == "hi"
        assert sent["message"]["status"] == "PENDING"
        assert sent["reopened"] is False

        key = (f"jobflows/instances/{instance_id}/tasks/dev/human/messages/"
               f"{sent['message']['message_id']}")
        assert key in store._store
        assert not any(k.startswith(f"workflows/{instance_id}") for k in store._store)

        status, listed = request(port, "GET", path)
        assert status == 200, listed
        assert [item["message"] for item in listed["messages"]] == ["hi"]
    finally:
        server.shutdown()
        server.server_close()


def test_interrupt_message_to_instance_task_is_accepted():
    _, server, port = _serve()
    try:
        instance_id = _create_instance(port)
        path = f"/api/workflow/{instance_id}/task/dev/messages"
        status, sent = request(port, "POST", path, {"message": "停下", "mode": "interrupt"})
        assert status == 202, sent
        assert sent["message"]["mode"] == "interrupt"
    finally:
        server.shutdown()
        server.server_close()


def test_message_to_terminal_instance_task_requeues_the_task():
    store, server, port = _serve()
    try:
        instance_id = _create_instance(port)
        base = f"jobflows/instances/{instance_id}/tasks/dev"
        store._store[f"{base}/status"] = json.dumps({
            "task_id": "dev", "state": "DONE", "attempt_count": 1,
        })

        status, sent = request(
            port, "POST", f"/api/workflow/{instance_id}/task/dev/messages",
            {"message": "再改一版"},
        )
        assert status == 202, sent
        assert sent["reopened"] is True
        reopened = json.loads(store._store[f"{base}/status"])
        assert reopened["state"] == "PENDING"
        assert reopened["attempt_count"] == 2
        assert reopened["current_attempt"]
    finally:
        server.shutdown()
        server.server_close()


def test_message_to_finished_instance_resumes_it_for_dispatch():
    """已结束实例收到人工消息后必须回到 RUNNING，否则任务永远停在 PENDING。"""
    store, server, port = _serve()
    try:
        instance_id = _create_instance(port)
        instance_base = f"jobflows/instances/{instance_id}"
        store._store[f"{instance_base}/status"] = json.dumps({
            "state": "SUCCEEDED", "revision": 5,
            "updated_at": "2026-10-16T14:02:26.634850Z",
        })
        # run 结束时 current_run 已被清掉，只剩 run/current 指针（线上实际形态）。
        store._store[f"{instance_base}/runs/run-finished/status"] = "COMPLETED"
        store._store[f"{instance_base}/runs/run-finished/finished_at"] = "2026-10-16T14:02:26Z"
        store._store[f"{instance_base}/run/current"] = json.dumps({
            "run_id": "run-finished", "status": "RUNNING",
        })
        store._store[f"{instance_base}/tasks/dev/status"] = json.dumps({
            "task_id": "dev", "state": "DONE", "attempt_count": 1,
        })

        status, sent = request(
            port, "POST", f"/api/workflow/{instance_id}/task/dev/messages",
            {"message": "hi"},
        )
        assert status == 202, sent
        assert sent["reopened"] is True
        assert sent["resumed"] is True

        assert json.loads(store._store[f"{instance_base}/status"])["state"] == "RUNNING"
        assert store._store[f"{instance_base}/runs/run-finished/status"] == "RUNNING"
        assert f"{instance_base}/runs/run-finished/finished_at" not in store._store
        assert json.loads(store._store[f"{instance_base}/tasks/dev/status"])["state"] == "PENDING"
    finally:
        server.shutdown()
        server.server_close()


def test_job_flow_session_events_are_served_from_instance_layout():
    """会话事件写在 jobflows/instances/ 下，/api/sessions 也必须从那里读。

    回归背景：该端点过去写死 `workflows/<req>/sessions/<task>/`，作业流实例
    的 Agent 时间线永远是空的——用户在任务页看不到任何 AI 返回。
    """
    store, server, port = _serve()
    try:
        instance_id = _create_instance(port)
        store.kv_put(
            f"jobflows/instances/{instance_id}/sessions/dev/"
            "session-1/events/000001",
            json.dumps({
                "timestamp": "2026-10-16T12:00:00Z", "type": "ACP_UPDATE",
                "provider": "codex", "run_id": "run-1", "attempt_id": "attempt-1",
                "payload": {"update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "你好，我是 AI"},
                }},
            }, ensure_ascii=False),
        )

        status, payload = request(port, "GET", f"/api/sessions/{instance_id}/dev")
        assert status == 200, payload
        assert payload["total"] == 1
        assert payload["events"][0]["step_type"] == "ASSISTANT_MSG"
        assert payload["events"][0]["message"] == "你好，我是 AI"

        status, filtered = request(
            port, "GET",
            f"/api/sessions/{instance_id}/dev?attempt_id=attempt-1",
        )
        assert status == 200
        assert filtered["total"] == 1
    finally:
        server.shutdown()
        server.server_close()


def test_message_to_unknown_instance_task_returns_404():
    _, server, port = _serve()
    try:
        instance_id = _create_instance(port)
        status, payload = request(
            port, "POST", f"/api/workflow/{instance_id}/task/nope/messages",
            {"message": "hi"},
        )
        assert status == 404 and payload["error"] == "task not found"
    finally:
        server.shutdown()
        server.server_close()


def test_legacy_workflow_task_message_still_uses_workflow_layout():
    store, server, port = _serve()
    try:
        _seed_legacy_workflow(store)
        path = "/api/workflow/req-legacy/task/build/messages"

        status, sent = request(port, "POST", path, {"message": "hi"})
        assert status == 202, sent
        assert sent["reopened"] is False
        key = f"workflows/req-legacy/tasks/build/human/messages/{sent['message']['message_id']}"
        assert key in store._store

        status, listed = request(port, "GET", path)
        assert status == 200
        assert [item["message"] for item in listed["messages"]] == ["hi"]
    finally:
        server.shutdown()
        server.server_close()
