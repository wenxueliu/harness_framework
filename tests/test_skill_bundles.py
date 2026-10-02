import pytest
from harness_framework.skill_bundles import SkillBundleService, SkillBundleError
from harness_framework.local_store import LocalStore

@pytest.fixture()
def svc():
    return SkillBundleService(LocalStore())

def test_register(svc):
    r = svc.register({"name": "tdd", "content": "use TDD", "version": "1.0.0"}, "admin")
    assert r["content_hash"] != ""
    assert "content" not in r

def test_duplicate_content_rejected(svc):
    svc.register({"name": "tdd", "content": "use TDD"}, "admin")
    with pytest.raises(SkillBundleError, match="BUNDLE_EXISTS"):
        svc.register({"name": "tdd-2", "content": "use TDD"}, "admin")

def test_get_content(svc):
    r = svc.register({"name": "tdd", "content": "use TDD"}, "admin")
    assert svc.get_content(r["bundle_id"]) == "use TDD"
