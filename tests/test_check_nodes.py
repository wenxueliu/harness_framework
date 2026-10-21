"""验证型节点（ralph-flow check）的框架原生语义。

- require_verdict: 结论不是 PASS 就判任务失败
- require_approval: 人工批准后才执行（批准只对本次有效）
- on_fail: 验证失败时由 Aggregator 回退上游任务重跑，受 max_fail_count 限制
"""
from __future__ import annotations

import json

from harness_framework.acp_client import ACPResult
from harness_framework.acp_dispatcher import ACPDispatcher
from harness_framework.aggregator import Aggregator
from harness_framework.job_flows import JobFlowService
from harness_framework.run_manager import RunManager
from tests.conftest import MockConsulStore
from tests.test_acp_dispatcher import FakeACPClient, _store
from tests.test_aggregator import _make_store, make_mock_run_manager


class VerdictACPClient(FakeACPClient):
    """Fake agent that answers with a configurable verdict."""

    verdict = "VERDICT: PASS"

    def prompt(self, text, *, timeout, should_cancel):
        update = {
            "sessionId": self.session_id,
            "update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text", "text": self.verdict}},
        }
        self.update_handler(update)
        self.prompts.append(text)
        return ACPResult(self.session_id, "end_turn", {"stopReason": "end_turn"},
                         [update])


def test_failed_verdict_fails_the_check_task():
    FakeACPClient.instances.clear()
    VerdictACPClient.instances.clear()
    VerdictACPClient.verdict = "VERDICT: FAIL 缺少测试"
    store = _store("backend")
    store.kv_put("workflows/req-1/tasks/build/require_verdict", "true")
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"codex": ["codex-acp"]},
        client_factory=VerdictACPClient,
    )

    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    dispatcher._active[(req_id, task_name)] = {**claim, "client": None}
    dispatcher._execute(req_id, task_name, meta, claim)

    assert store._store["workflows/req-1/tasks/build/status"] == "FAILED"
    assert "缺少测试" in store._store["workflows/req-1/tasks/build/error_message"]


def test_passed_verdict_completes_the_check_task():
    FakeACPClient.instances.clear()
    VerdictACPClient.instances.clear()
    VerdictACPClient.verdict = "VERDICT: PASS"
    store = _store("backend")
    store.kv_put("workflows/req-1/tasks/build/require_verdict", "true")
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"codex": ["codex-acp"]},
        client_factory=VerdictACPClient,
    )

    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    dispatcher._active[(req_id, task_name)] = {**claim, "client": None}
    dispatcher._execute(req_id, task_name, meta, claim)

    assert store._store["workflows/req-1/tasks/build/status"] == "DONE"


def test_approval_gate_holds_the_task_until_approved():
    FakeACPClient.instances.clear()
    VerdictACPClient.instances.clear()
    store = _store("backend")
    store.kv_put("workflows/req-1/tasks/build/require_approval", "true")
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"codex": ["codex-acp"]},
        client_factory=VerdictACPClient,
    )

    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)

    assert claim is None
    assert store._store["workflows/req-1/tasks/build/status"] == "AWAITING_REVIEW"
    assert not VerdictACPClient.instances

    # 人工批准后放行；批准只对本次执行有效
    store.kv_put("workflows/req-1/tasks/build/approval", json.dumps({"by": "human"}))
    store.kv_put("workflows/req-1/tasks/build/status", "PENDING")
    claim = dispatcher._claim(req_id, task_name, meta)
    assert claim is not None
    assert "workflows/req-1/tasks/build/approval" not in store._store


def _check_instance(fail_count: int = 0) -> dict:
    return {
        "jobflows/instances/inst-010/status": json.dumps(
            {"state": "RUNNING", "revision": 1}
        ),
        "jobflows/instances/inst-010/tasks/explore/definition": json.dumps(
            {"id": "explore", "depends_on": []}
        ),
        "jobflows/instances/inst-010/tasks/explore/status": json.dumps(
            {"state": "DONE", "attempt_count": 1}
        ),
        "jobflows/instances/inst-010/tasks/explore__check/definition": json.dumps({
            "id": "explore__check", "depends_on": ["explore"],
            "on_fail": {"task": "explore", "max_fail_count": 2},
        }),
        "jobflows/instances/inst-010/tasks/explore__check/status": json.dumps(
            {"state": "FAILED", "attempt_count": 1, "fail_count": fail_count}
        ),
        "jobflows/instances/inst-010/tasks/propose/definition": json.dumps(
            {"id": "propose", "depends_on": ["explore__check"]}
        ),
        "jobflows/instances/inst-010/tasks/propose/status": json.dumps(
            {"state": "BLOCKED", "attempt_count": 0}
        ),
    }


def test_failed_check_falls_back_to_the_upstream_task():
    consul = _make_store(_check_instance())
    agg = Aggregator(consul, run_manager=make_mock_run_manager(), poll_interval=1)

    agg._process_instance("inst-010")

    puts = {call[0][0]: call[0][1] for call in consul.kv_put.call_args_list}
    explore = json.loads(puts["jobflows/instances/inst-010/tasks/explore/status"])
    check = json.loads(puts["jobflows/instances/inst-010/tasks/explore__check/status"])
    assert explore["state"] == "PENDING"
    assert check["state"] == "BLOCKED"
    assert check["fail_count"] == 1
    # 下游节点不能因为上游曾失败而被跳过
    propose = json.loads(puts.get(
        "jobflows/instances/inst-010/tasks/propose/status", "{}"))
    assert propose.get("state") != "SKIPPED_UPSTREAM_FAILED"


def test_failed_check_beyond_budget_stays_failed():
    consul = _make_store(_check_instance(fail_count=2))
    agg = Aggregator(consul, run_manager=make_mock_run_manager(), poll_interval=1)

    agg._process_instance("inst-010")

    puts = {call[0][0]: call[0][1] for call in consul.kv_put.call_args_list}
    assert "jobflows/instances/inst-010/tasks/explore/status" not in puts
    propose = json.loads(puts.get(
        "jobflows/instances/inst-010/tasks/propose/status", "{}"))
    assert propose.get("state") == "SKIPPED_UPSTREAM_FAILED"


def test_approve_task_releases_a_waiting_check():
    store = MockConsulStore({
        "jobflows/templates/tpl-1/meta": json.dumps({
            "template_id": "tpl-1", "name": "demo", "group_id": "unassigned",
            "status": "ACTIVE", "current_version_id": "v1",
        }),
        "jobflows/templates/tpl-1/versions/v1/manifest": json.dumps({
            "tasks": [{"id": "propose__check", "depends_on": []}],
        }),
        "jobflows/templates/tpl-1/versions/index": json.dumps([{
            "version_id": "v1", "template_id": "tpl-1", "version": 1,
            "status": "PUBLISHED", "manifest_hash": "hash",
        }]),
        "jobflows/templates/tpl-1/versions/v1/status": json.dumps({
            "version_id": "v1", "template_id": "tpl-1", "version": 1,
            "status": "PUBLISHED", "manifest_hash": "hash",
        }),
        "jobflows/instances/inst-020/meta": json.dumps({
            "instance_id": "inst-020", "template_id": "tpl-1",
            "version_id": "v1", "name": "demo", "group_id": "unassigned",
        }),
        "jobflows/instances/inst-020/status": json.dumps({"state": "RUNNING"}),
        "jobflows/instances/inst-020/tasks/propose__check/status": json.dumps({
            "task_id": "propose__check", "state": "AWAITING_REVIEW",
        }),
    })
    service = JobFlowService(store)

    result = service.approve_task("inst-020", "propose__check", "local:human")

    assert result["approved"] is True
    status = json.loads(store._store[
        "jobflows/instances/inst-020/tasks/propose__check/status"])
    assert status["state"] == "PENDING"
    assert store._store["jobflows/instances/inst-020/tasks/propose__check/approval"]
