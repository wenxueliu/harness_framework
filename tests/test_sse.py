from __future__ import annotations

import json
import threading
import urllib.request
import urllib.error

from harness_framework.event_journal import EventJournal
from harness_framework.local_store import LocalStore
from harness_framework.webapi import serve
from harness_framework.auth import AuthConfig


def test_real_http_sse_delivers_event_and_honors_last_event_id(tmp_path):
    store = LocalStore()
    journal = EventJournal(store)
    first = journal.append(
        "RUN_CREATED", subject={"req_id": "req-1"},
        actor={"type": "human", "id": "local:test"},
    )
    second = journal.append(
        "TASK_STATUS_CHANGED", subject={"req_id": "req-1", "task_id": "build"},
        actor={"type": "agent", "id": "agent-1"},
    )
    server = serve(
        store, host="127.0.0.1", port=0, sse_connection_seconds=0.05
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/events?req_id=req-1",
            headers={"Last-Event-ID": first.event_id},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            body = response.read().decode()
        assert response.headers["Content-Type"].startswith("text/event-stream")
        assert f"id: {second.event_id}" in body
        assert "event: TASK_STATUS_CHANGED" in body
        assert f"id: {first.event_id}" not in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_sse_unknown_cursor_requests_snapshot_reset():
    store = LocalStore()
    server = serve(store, host="127.0.0.1", port=0, sse_connection_seconds=0.05)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/events",
            headers={"Last-Event-ID": "evt_missing"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            body = response.read().decode()
        assert "event: STREAM_RESET_REQUIRED" in body
        payload = json.loads(body.split("data: ", 1)[1].split("\n", 1)[0])
        assert payload["reason"] == "EVENT_CURSOR_EXPIRED"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_trusted_proxy_sse_requires_authorized_scope():
    store = LocalStore()
    auth = AuthConfig(mode="trusted-proxy", trusted_proxy_addresses=frozenset({"127.0.0.1"}))
    server = serve(store, host="127.0.0.1", port=0, auth_config=auth, sse_connection_seconds=0.01)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/events",
            headers={"X-Harness-Subject": "user:viewer"},
        )
        try:
            urllib.request.urlopen(request, timeout=2)
            assert False, "unscoped trusted-proxy stream must be rejected"
        except urllib.error.HTTPError as error:
            assert error.code == 422
            body = json.loads(error.read().decode())
            assert body["error"]["code"] == "EVENT_SCOPE_REQUIRED"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_event_replay_pages_past_first_thousand_records():
    store = LocalStore()
    journal = EventJournal(store)
    cursor = None
    for sequence in range(1005):
        event = journal.append(
            "SESSION_EVENT", subject={"req_id": "req-1"},
            actor={"type": "agent", "id": "agent-1"},
            data={"value": sequence},
        )
        if sequence == 1001:
            cursor = event.event_id
    events, reset = journal.replay(after_event_id=cursor, limit=10)
    assert reset is False
    assert [event.data["value"] for event in events] == [1002, 1003, 1004]
