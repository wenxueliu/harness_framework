from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "skills/stage-bridge/scripts/record_evaluation.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("record_evaluation_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_cli_persists_fallback_then_human_escalation(monkeypatch):
    module = _load_module()
    base = "workflows/req-1/tasks/task-1"
    store = {
        f"{base}/evaluator_policy": json.dumps({
            "max_iterations": 2,
            "plateau_window": 2,
            "plateau_delta": 0,
            "fallback_chain": ["primary", "narrowed"],
            "escalation_target": "owner",
        }),
    }
    indices = {key: 1 for key in store}
    emitted = []

    def kv_get(key):
        return store.get(key), indices.get(key, 0)

    def kv_put(key, value, cas=None):
        if cas is not None and indices.get(key, 0) != cas:
            return False
        store[key] = value
        indices[key] = indices.get(key, 0) + 1
        return True

    monkeypatch.setattr(module, "kv_get", kv_get)
    monkeypatch.setattr(module, "kv_put", kv_put)
    monkeypatch.setattr(module, "validate_attempt", lambda *args: (True, ""))
    monkeypatch.setattr(module, "emit_json", emitted.append)

    for expected in ("RETRY", "SWITCH_FALLBACK", "RETRY", "ESCALATE"):
        monkeypatch.setattr(
            "sys.argv",
            ["record_evaluation.py", "req-1", "task-1", "0.5", "FAIL",
             "--attempt-id", "attempt-1", "--lease-epoch", "1"],
        )
        module.main()
        assert emitted[-1]["state"]["status"] == expected

    escalation = json.loads(store[f"{base}/evaluator/escalation"])
    assert escalation["target"] == "owner"
    assert escalation["status"] == "OPEN"
    assert any("/human_interventions/task-1/" in key for key in store)


def test_cli_rejects_observation_after_terminal_state(monkeypatch):
    module = _load_module()
    base = "workflows/req-1/tasks/task-1"
    values = {
        f"{base}/evaluator_policy": "{}",
        f"{base}/evaluator/state": json.dumps({"status": "PASS"}),
    }
    monkeypatch.setattr(module, "kv_get", lambda key: (values.get(key), 1))
    monkeypatch.setattr(module, "validate_attempt", lambda *args: (True, ""))
    emitted = []
    monkeypatch.setattr(module, "emit_json", emitted.append)
    monkeypatch.setattr(
        "sys.argv",
        ["record_evaluation.py", "req-1", "task-1", "1", "PASS",
         "--attempt-id", "attempt-1", "--lease-epoch", "1"],
    )

    try:
        module.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("terminal evaluator state accepted another observation")
    assert "terminal" in emitted[-1]["error"]
