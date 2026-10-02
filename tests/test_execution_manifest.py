import pytest
from harness_framework.execution_manifest import ExecutionManifestService, ManifestError
from harness_framework.local_store import LocalStore

@pytest.fixture()
def svc():
    return ExecutionManifestService(LocalStore())

def _runtime(**kw): return {"name": "claude-code", "version": "1.2", **kw}
def _profile(**kw): return {"name": "std", "version": "1.0.0", **kw}

def test_generate(svc):
    m = svc.generate("attempt-1", _runtime(), _profile(), [], [], {})
    assert m["manifest_id"] != ""
    assert m["resolved_by"] == "system"

def test_immutable(svc):
    svc.generate("attempt-1", _runtime(), _profile(), [], [], {})
    with pytest.raises(ManifestError, match="MANIFEST_ALREADY_EXISTS"):
        svc.generate("attempt-1", _runtime(), _profile(), [], [], {})

def test_retry_with_previous(svc):
    svc.generate("attempt-1", _runtime(version="1.0"), _profile(), [], [], {})
    m2 = svc.generate("attempt-2", _runtime(version="1.2"), _profile(), [], [], {},
                      previous_manifest_id="mf-first")
    assert m2["resolved_by"] == "manual_retry"
    assert m2["previous_manifest_id"] == "mf-first"

def test_get_not_found(svc):
    with pytest.raises(ManifestError, match="MANIFEST_NOT_FOUND"):
        svc.get("nonexistent")
