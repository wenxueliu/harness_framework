from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).parents[1] / "skills/stage-bridge/scripts"
sys.path.insert(0, str(SCRIPTS))
import _consul  # noqa: E402


def _reader(values: dict[str, str]):
    def kv_get(key: str, recurse: bool = False):
        if recurse:
            prefix = key.rstrip("/") + "/"
            items = [
                {"Key": item_key, "_decoded": value}
                for item_key, value in values.items()
                if item_key.startswith(prefix)
            ]
            return items or None, 1
        return values.get(key), 1
    return kv_get


def test_declared_context_resolves_fact_current_artifact_and_summary(monkeypatch):
    task = "workflows/req-1/tasks/test"
    root = "workflows/req-1/knowledge"
    values = {
        f"{task}/context_inputs": json.dumps([
            "facts/customer", "artifacts/api-spec", "summaries/design",
        ]),
        f"{root}/facts/customer": '{"value":"c-1"}',
        f"{root}/artifacts/api-spec/current": json.dumps({"version_id": "v2-x"}),
        f"{root}/artifacts/api-spec/versions/v2-x/value": '{"openapi":"3.1"}',
        f"{root}/summaries/design": '{"value":"short"}',
        f"{root}/facts/not-declared": "must-not-leak",
    }
    monkeypatch.setattr(_consul, "kv_get", _reader(values))

    context = _consul.load_declared_context("req-1", "test")

    assert set(context) == {
        "facts/customer", "artifacts/api-spec", "summaries/design",
    }
    assert "must-not-leak" not in str(context)


def test_missing_declaration_injects_nothing(monkeypatch):
    monkeypatch.setattr(_consul, "kv_get", _reader({
        "workflows/req-1/knowledge/facts/secret": "must-not-leak",
    }))
    assert _consul.load_declared_context("req-1", "task") == {}


@pytest.mark.parametrize("selector", [
    "restricted/token", "events/task/*", "working_memory/other/scratch",
])
def test_unsafe_context_selector_is_rejected(monkeypatch, selector):
    monkeypatch.setattr(_consul, "kv_get", _reader({
        "workflows/req-1/tasks/task/context_inputs": json.dumps([selector]),
    }))
    with pytest.raises(PermissionError):
        _consul.load_declared_context("req-1", "task")
