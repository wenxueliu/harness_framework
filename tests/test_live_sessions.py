from __future__ import annotations

from harness_framework.live_sessions import LiveSessionHub


def _update(text: str) -> dict:
    return {"update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text", "text": text}}}


def test_snapshot_returns_incremental_events_after_cursor():
    hub = LiveSessionHub()
    for index in range(3):
        hub.publish("inst-1", "build", attempt_id="att-1", session_id="ses-1",
                     payload=_update(str(index)))

    first = hub.snapshot("inst-1", "build")
    assert [event["seq"] for event in first["events"]] == [1, 2, 3]
    assert first["cursor"] == 3
    assert first["attempt_id"] == "att-1"
    assert first["session_id"] == "ses-1"

    hub.publish("inst-1", "build", attempt_id="att-1", session_id="ses-1",
                payload=_update("3"))
    tail = hub.snapshot("inst-1", "build", after=first["cursor"])
    assert [event["seq"] for event in tail["events"]] == [4]
    assert tail["truncated"] is False


def test_snapshot_marks_truncated_when_ring_buffer_dropped_events():
    hub = LiveSessionHub(max_events=3)
    for index in range(5):
        hub.publish("inst-1", "build", payload=_update(str(index)))

    payload = hub.snapshot("inst-1", "build", after=1)

    assert payload["truncated"] is True
    assert [event["seq"] for event in payload["events"]] == [3, 4, 5]


def test_snapshot_isolated_per_task_and_reports_active():
    hub = LiveSessionHub()
    hub.publish("inst-1", "build", payload=_update("a"))

    assert hub.snapshot("inst-1", "test")["events"] == []
    assert hub.snapshot("inst-1", "build")["active"] is True


def test_clear_removes_task_buffer():
    hub = LiveSessionHub()
    hub.publish("inst-1", "build", payload=_update("a"))
    hub.clear("inst-1", "build")

    payload = hub.snapshot("inst-1", "build")
    assert payload["events"] == []
    assert payload["active"] is False


def test_max_tasks_evicts_oldest_buffer():
    hub = LiveSessionHub(max_tasks=2)
    hub.publish("inst-1", "a", payload=_update("a"))
    hub.publish("inst-1", "b", payload=_update("b"))
    hub.publish("inst-1", "c", payload=_update("c"))

    assert hub.snapshot("inst-1", "a")["events"] == []
    assert len(hub.snapshot("inst-1", "c")["events"]) == 1
