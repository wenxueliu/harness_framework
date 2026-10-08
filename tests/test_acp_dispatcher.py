from __future__ import annotations

import json

from harness_framework.acp_client import ACPResult
from harness_framework.acp_dispatcher import ACPDispatcher, JobFlowACPDispatcher
from harness_framework.job_flows import JobFlowService
from harness_framework.human_interaction import create_human_message, list_human_messages
from harness_framework.run_manager import RunManager
from harness_framework.project_groups import ProjectGroupService
from harness_framework.workspace_manager import WorkspaceManager
from harness_framework.workspace_security import WorkspaceSecurity
from tests.conftest import MockConsulStore


class FakeACPClient:
    instances = []

    def __init__(self, command, *, cwd, env, permission_policy, update_handler):
        self.command = command
        self.cwd = cwd
        self.env = env
        self.update_handler = update_handler
        self.session_id = ""
        self.cancelled = False
        self.prompts = []
        self.__class__.instances.append(self)

    def start(self):
        pass

    def initialize(self):
        return {"protocolVersion": 1, "agentInfo": {"name": "fake"}}

    def new_session(self):
        self.session_id = "acp-session-new"
        return self.session_id

    def load_session(self, session_id):
        self.session_id = session_id
        return session_id

    def prompt(self, text, *, timeout, should_cancel):
        assert "TASK PACKAGE" in text or "HUMAN MESSAGE" in text
        assert not should_cancel()
        self.prompts.append(text)
        update = {
            "sessionId": self.session_id,
            "update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text", "text": "done"}},
        }
        self.update_handler(update)
        return ACPResult(self.session_id, "end_turn", {"stopReason": "end_turn"}, [update])

    def cancel(self):
        self.cancelled = True

    def close(self):
        pass


def _store(task_type="backend", acp=None):
    base = "workflows/req-1/tasks/build"
    values = {
        "workflows/req-1/published": "true",
        "workflows/req-1/status": "IN_PROGRESS",
        f"{base}/status": "PENDING",
        f"{base}/type": task_type,
        f"{base}/description": "Implement the requested change",
        f"{base}/context_inputs": "[]",
    }
    if acp is not None:
        values[f"{base}/acp"] = json.dumps(acp)
    return MockConsulStore(values)


def test_task_type_selects_agent_and_dispatch_completes():
    FakeACPClient.instances.clear()
    store = _store("backend")
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    assert claim["provider"] == "codex"
    dispatcher._active[(req_id, task_name)] = {**claim, "client": None}
    dispatcher._execute(req_id, task_name, meta, claim)

    base = "workflows/req-1/tasks/build"
    assert store._store[f"{base}/status"] == "DONE"
    assert store._store[f"{base}/execution_transport"] == "acp"
    assert store._store[f"{base}/acp/session_id"] == "acp-session-new"
    assert FakeACPClient.instances[0].command == ["codex-acp"]
    assert not any(key.startswith("agents/") for key in store._store)


def test_managed_workspace_task_is_not_claimed_before_run_is_active():
    store = _store("backend")
    store._store["workflows/req-1/execution_mode"] = "managed-workspace"
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    assert dispatcher._pending_tasks() == []
    assert not any("/runs/" in key for key in store._store)


def test_managed_workspace_task_requires_ready_workspace_record():
    store = _store("backend")
    store._store["workflows/req-1/execution_mode"] = "managed-workspace"
    run_id = RunManager(store).create_provisioning_run(
        "req-1", "user:alice", idempotency_key="one"
    )
    store._store[f"workflows/req-1/current_run"] = run_id
    store._store[f"workflows/req-1/runs/{run_id}/status"] = "RUNNING"
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    assert dispatcher._pending_tasks() == []
    store._store[f"workflows/req-1/runs/{run_id}/workspace/record"] = json.dumps({
        "status": "READY"
    })
    assert len(dispatcher._pending_tasks()) == 1


def _managed_dispatcher(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = _store("backend")
    store.kv_put("workflows/req-1/dependencies", json.dumps({
        "build": {"type": "backend", "depends_on": []}
    }))
    groups = ProjectGroupService(store)
    group = groups.create(name="Core", description="", actor="local:alice")
    groups.assign_primary_workflow(group["group_id"], "req-1")
    manager = WorkspaceManager(
        store, WorkspaceSecurity({"projects": str(tmp_path)}), groups,
        provision_root_alias="projects",
    )
    project = manager.register_local(
        group_id=group["group_id"], name="Repo", root_alias="projects",
        relative_path="repo",
    )
    runs = RunManager(store)
    store.kv_put("workflows/req-1/execution_mode", "managed-workspace")
    run_id = runs.create_provisioning_run(
        "req-1", "local:alice", idempotency_key="managed-one"
    )
    manager.provision_run_workspace(
        req_id="req-1", run_id=run_id,
        project_workspace_id=project["workspace_id"], strategy="ORIGINAL",
    )
    runs.activate_provisioned_run("req-1", run_id)
    dispatcher = ACPDispatcher(
        store, runs,
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient, workspace_manager=manager,
    )
    return store, manager, dispatcher, repo, run_id


def test_managed_attempt_binds_agent_to_run_workspace(tmp_path):
    FakeACPClient.instances.clear()
    store, manager, dispatcher, repo, run_id = _managed_dispatcher(tmp_path)
    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    assert claim is not None
    binding = manager.get_attempt_binding(
        req_id, run_id, task_name, claim["attempt_id"]
    )
    dispatcher._active[(req_id, task_name)] = {**claim, "client": None}
    dispatcher._execute(req_id, task_name, meta, claim)

    assert binding["binding_type"] == "RUN_SHARED"
    assert FakeACPClient.instances[0].cwd == str(repo)
    assert store._store[f"workflows/{req_id}/tasks/{task_name}/status"] == "DONE"


def test_missing_managed_workspace_waits_for_human(tmp_path):
    FakeACPClient.instances.clear()
    store, _, dispatcher, repo, _ = _managed_dispatcher(tmp_path)
    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    assert claim is not None
    repo.rmdir()
    dispatcher._active[(req_id, task_name)] = {**claim, "client": None}
    dispatcher._execute(req_id, task_name, meta, claim)

    base = f"workflows/{req_id}/tasks/{task_name}"
    assert store._store[f"{base}/status"] == "WAITING_FOR_HUMAN"
    assert store._store[f"{base}/waiting_reason"] == "WORKSPACE_UNAVAILABLE"
    assert FakeACPClient.instances == []


def test_task_acp_override_selects_claude_and_continues_session():
    FakeACPClient.instances.clear()
    store = _store("backend", {
        "agent": "claude",
        "session": {"mode": "continue", "from_task": "design"},
    })
    store._store["workflows/req-1/tasks/design/acp/session_id"] = "prior-session"
    store._store["workflows/req-1/tasks/design/acp/provider"] = "claude"
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    dispatcher._active[(req_id, task_name)] = {**claim, "client": None}
    dispatcher._execute(req_id, task_name, meta, claim)

    assert claim["provider"] == "claude"
    assert FakeACPClient.instances[0].session_id == "prior-session"


def test_pending_human_message_resumes_own_session_as_new_attempt():
    FakeACPClient.instances.clear()
    store = _store("backend")
    base = "workflows/req-1/tasks/build"
    store._store[f"{base}/acp/session_id"] = "existing-session"
    create_human_message(
        store, "req-1", "build", message="Use JWT instead", actor="alice",
    )
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    dispatcher._active[(req_id, task_name)] = {**claim, "client": None}

    dispatcher._execute(req_id, task_name, meta, claim)

    client = FakeACPClient.instances[0]
    assert client.session_id == "existing-session"
    assert len(client.prompts) == 1
    assert "Use JWT instead" in client.prompts[0]
    message = list_human_messages(store, "req-1", "build")[0]
    assert message["status"] == "APPLIED"
    assert message["response"] == "done"
    assert store._store[f"{base}/status"] == "DONE"


def test_continue_session_rejects_provider_mismatch():
    store = _store("backend", {
        "agent": "codex",
        "session": {"mode": "continue", "from_task": "design"},
    })
    store._store["workflows/req-1/tasks/design/acp/session_id"] = "prior-session"
    store._store["workflows/req-1/tasks/design/acp/provider"] = "claude"
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    dispatcher._active[(req_id, task_name)] = {**claim, "client": None}
    dispatcher._execute(req_id, task_name, meta, claim)

    base = "workflows/req-1/tasks/build"
    assert store._store[f"{base}/status"] == "FAILED"
    assert "different provider" in store._store[f"{base}/error_message"]


def test_context_resolves_versioned_artifact_and_wildcard():
    store = _store()
    root = "workflows/req-1/knowledge"
    store._store[f"{root}/artifacts/api/current"] = json.dumps({"version_id": "v2"})
    store._store[f"{root}/artifacts/api/versions/v2/value"] = "openapi: 3.1"
    store._store[f"{root}/facts/a"] = "one"
    store._store[f"{root}/facts/b"] = "two"
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    context = dispatcher._load_context("req-1", "build", {
        "context_inputs": json.dumps(["artifacts/api", "facts/*"])
    })

    assert context["artifacts/api"] == "openapi: 3.1"
    assert context["facts/a"] == "one"
    assert context["facts/b"] == "two"


def test_completion_contract_is_enforced():
    store = _store()
    base = "workflows/req-1/tasks/build"
    store._store[f"{base}/completion_contract"] = json.dumps({
        "required_artifacts": ["implementation"], "required_gates": ["tests"]
    })
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    dispatcher._active[(req_id, task_name)] = {**claim, "client": None}
    dispatcher._execute(req_id, task_name, meta, claim)
    assert store._store[f"{base}/status"] == "FAILED"
    assert "artifact:implementation" in store._store[f"{base}/error_message"]


def _job_flow_service(store):
    FakeACPClient.instances.clear()
    service = JobFlowService(store)
    service.create_template({
        "template_id": "tpl-acp", "name": "ACP Flow",
        "tasks": [
            {"id": "build", "type": "backend", "depends_on": []},
            {"id": "test", "type": "test", "depends_on": ["build"]},
        ],
    }, "local:alice")
    version = service.publish_template(
        "tpl-acp", {"expected_revision": 1}, "local:alice", "publish-acp",
    )["version"]
    service.create_instance({
        "template_id": "tpl-acp", "version_id": version["version_id"],
        "instance_id": "inst-acp", "name": "acp-main",
    }, "local:alice", "create-acp")
    service.start_instance("inst-acp", "local:alice")
    return service


def test_job_flow_pending_task_dispatches_and_completes_through_acp():
    FakeACPClient.instances.clear()
    store = MockConsulStore()
    service = _job_flow_service(store)
    run = json.loads(store._store["jobflows/instances/inst-acp/run/current"])
    started = {"run": run}
    assert run["status"] == "RUNNING"

    dispatcher = JobFlowACPDispatcher(
        store,
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    from harness_framework.aggregator import Aggregator
    aggregator = Aggregator(store, RunManager(store, instance_mode=True))
    aggregator._tick_instances()

    for expected_task, expected_type in (("build", "backend"), ("test", "test")):
        instance_id, task_name, meta = dispatcher._pending_tasks()[0]
        assert (instance_id, task_name) == ("inst-acp", expected_task)
        assert meta["type"] == expected_type

        claim = dispatcher._claim(instance_id, task_name, meta)
        assert claim is not None and claim["provider"] == "codex"
        assert claim["run_id"] == started["run"]["run_id"]
        dispatcher._active[(f"jobflow:{instance_id}", task_name)] = {
            **claim, "scope_id": instance_id, "client": None,
        }
        dispatcher._execute(instance_id, task_name, meta, claim)

        base = f"jobflows/instances/{instance_id}/tasks/{task_name}"
        task_status = json.loads(store._store[f"{base}/status"])
        assert task_status["state"] == "DONE"
        assert task_status["current_attempt"] == claim["attempt_id"]
        assert store._store[f"{base}/execution_transport"] == "acp"
        assert store._store[f"{base}/acp/session_id"] == "acp-session-new"
        assert (f"jobflow:{instance_id}", task_name) not in dispatcher._active
        assert not any(key.startswith("workflows/") for key in store._store)
        aggregator._tick_instances()

    instance_status = json.loads(store._store["jobflows/instances/inst-acp/status"])
    assert instance_status["state"] == "SUCCEEDED"


def test_job_flow_abort_blocks_dispatch_and_finalizes_run():
    store = MockConsulStore()
    service = _job_flow_service(store)
    service.control_instance("inst-acp", "abort", "local:alice")

    dispatcher = JobFlowACPDispatcher(
        store,
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    assert dispatcher._pending_tasks() == []
    instance_status = json.loads(store._store["jobflows/instances/inst-acp/status"])
    task_status = json.loads(store._store["jobflows/instances/inst-acp/tasks/build/status"])
    assert instance_status["state"] == "ABORTED"
    assert task_status["state"] == "ABORTED"
    run = json.loads(store._store["jobflows/instances/inst-acp/run/current"])
    assert run["status"] == "ABORTED"
    assert store._store[f"jobflows/instances/inst-acp/runs/{run['run_id']}/status"] == "ABORTED"
    assert not store._store.get("jobflows/instances/inst-acp/current_run")


def test_job_flow_retry_requeues_task_for_acp_dispatch():
    store = MockConsulStore()
    service = _job_flow_service(store)
    response = service.retry_task("inst-acp", "build", "local:alice")

    dispatcher = JobFlowACPDispatcher(
        store,
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    pending = dispatcher._pending_tasks()
    assert [(instance_id, task_id) for instance_id, task_id, _ in pending] == [
        ("inst-acp", "build")
    ]
    assert pending[0][2]["current_attempt"] == response["attempt_id"]


def test_job_flow_expired_lease_is_recovered_to_pending():
    store = MockConsulStore()
    service = _job_flow_service(store)
    dispatcher = JobFlowACPDispatcher(
        store,
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
        client_factory=FakeACPClient,
    )
    instance_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(instance_id, task_name, meta)
    assert claim is not None
    base = dispatcher._task_base(instance_id, task_name)
    store.kv_put(f"{base}/lease_expires_at", "2000-01-01T00:00:00Z")

    dispatcher._tick()

    status = json.loads(store._store[f"{base}/status"])
    assert status["state"] == "PENDING"
    assert status["lease_epoch"] == claim["lease_epoch"] + 1
