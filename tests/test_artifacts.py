import pytest
from harness_framework.artifacts import ArtifactService, ArtifactError
from harness_framework.local_store import LocalStore

@pytest.fixture()
def svc():
    return ArtifactService(LocalStore())

def test_create(svc):
    r = svc.create({"artifact_id": "art-1", "instance_id": "inst-1", "path": "out/log.txt", "retention_days": 7})
    assert r["status"] == "ACTIVE"
    assert r["retention_until"] > r["created_at"]

def test_expire(svc):
    svc.create({"artifact_id": "art-1", "instance_id": "inst-1", "path": "out/log.txt"})
    r = svc.expire("inst-1", "art-1")
    assert r["status"] == "EXPIRED"

def test_purge_requires_expired(svc):
    svc.create({"artifact_id": "art-1", "instance_id": "inst-1", "path": "out/log.txt"})
    with pytest.raises(ArtifactError, match="NOT_EXPIRED"):
        svc.purge("inst-1", "art-1")

def test_purge_after_expire(svc):
    svc.create({"artifact_id": "art-1", "instance_id": "inst-1", "path": "out/log.txt"})
    svc.expire("inst-1", "art-1")
    svc.purge("inst-1", "art-1")
    with pytest.raises(ArtifactError, match="ARTIFACT_NOT_FOUND"):
        svc.get("inst-1", "art-1")

def test_list_for_instance(svc):
    svc.create({"artifact_id": "art-1", "instance_id": "inst-1", "path": "a.txt"})
    svc.create({"artifact_id": "art-2", "instance_id": "inst-1", "path": "b.txt"})
    assert len(svc.list_for_instance("inst-1")) == 2
