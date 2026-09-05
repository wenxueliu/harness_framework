from __future__ import annotations

import json

from harness_framework.acp_client import ACPResult
from harness_framework.acp_dispatcher import ACPDispatcher
from harness_framework.run_manager import RunManager
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
        assert "TASK PACKAGE" in text
        assert not should_cancel()
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
