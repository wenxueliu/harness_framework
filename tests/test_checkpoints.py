from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).parents[1] / "skills/stage-bridge/scripts"
sys.path.insert(0, str(SCRIPTS))
import _consul  # noqa: E402


def test_latest_checkpoint_is_verified_before_resume(monkeypatch):
    payload = '{"offset":10}'
    checksum = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    base = "workflows/req-1/tasks/import/checkpoints"
    values = {
        f"{base}/current_version": "2",
        f"{base}/versions/2/payload": payload,
        f"{base}/versions/2/manifest": json.dumps({
            "checkpoint_version": 2, "cursor": "batch:10", "checksum": checksum,
        }),
    }
    monkeypatch.setattr(_consul, "kv_get", lambda key, **kwargs: (values.get(key), 1))

    checkpoint = _consul.load_latest_checkpoint("req-1", "import")

    assert checkpoint["version"] == 2
    assert checkpoint["manifest"]["cursor"] == "batch:10"
    assert checkpoint["payload"] == payload


def test_corrupt_checkpoint_is_rejected(monkeypatch):
    base = "workflows/req-1/tasks/import/checkpoints"
    values = {
        f"{base}/current_version": "1",
        f"{base}/versions/1/payload": "changed",
        f"{base}/versions/1/manifest": json.dumps({"checksum": "sha256:bad"}),
    }
    monkeypatch.setattr(_consul, "kv_get", lambda key, **kwargs: (values.get(key), 1))
    with pytest.raises(ValueError, match="checksum mismatch"):
        _consul.load_latest_checkpoint("req-1", "import")


def test_task_without_checkpoint_starts_fresh(monkeypatch):
    monkeypatch.setattr(_consul, "kv_get", lambda key, **kwargs: (None, 1))
    assert _consul.load_latest_checkpoint("req-1", "new") is None
