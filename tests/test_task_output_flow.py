"""产物契约在调度层的落地：output_format 校验 + 产物传递给下游。"""
from __future__ import annotations

import json

from harness_framework.acp_client import ACPResult
from harness_framework.acp_dispatcher import ACPDispatcher
from harness_framework.run_manager import RunManager
from tests.test_acp_dispatcher import FakeACPClient, _store


class OutputACPClient(FakeACPClient):
    text = "OUTPUT: OK"

    def prompt(self, text, *, timeout, should_cancel):
        update = {
            "sessionId": self.session_id,
            "update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text", "text": self.text}},
        }
        self.update_handler(update)
        self.prompts.append(text)
        return ACPResult(self.session_id, "end_turn", {"stopReason": "end_turn"},
                         [update])


def _dispatcher(store):
    return ACPDispatcher(
        store, RunManager(store),
        commands={"codex": ["codex-acp"]},
        client_factory=OutputACPClient,
    )


def test_matching_output_format_completes_and_records_the_output():
    FakeACPClient.instances.clear()
    OutputACPClient.instances.clear()
    OutputACPClient.text = "OUTPUT: OK"
    store = _store("backend")
    store.kv_put("workflows/req-1/tasks/build/output", "结果")
    store.kv_put("workflows/req-1/tasks/build/output_format",
                 json.dumps({"kind": "marker", "pattern": "OUTPUT: OK"}))
    dispatcher = _dispatcher(store)

    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    dispatcher._active[(req_id, task_name)] = {**claim, "client": None}
    dispatcher._execute(req_id, task_name, meta, claim)

    assert store._store["workflows/req-1/tasks/build/status"] == "DONE"
    record = json.loads(store._store["workflows/req-1/tasks/build/output"])
    assert record["declared"] == "结果"
    assert record["evidence"]["matched"] == "OUTPUT: OK"


def test_mismatched_output_format_fails_the_task():
    FakeACPClient.instances.clear()
    OutputACPClient.instances.clear()
    OutputACPClient.text = "我完成了"
    store = _store("backend")
    store.kv_put("workflows/req-1/tasks/build/output", "结果")
    store.kv_put("workflows/req-1/tasks/build/output_format",
                 json.dumps({"kind": "marker", "pattern": "OUTPUT: OK"}))
    dispatcher = _dispatcher(store)

    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    dispatcher._active[(req_id, task_name)] = {**claim, "client": None}
    dispatcher._execute(req_id, task_name, meta, claim)

    assert store._store["workflows/req-1/tasks/build/status"] == "FAILED"
    assert "产物校验未通过" in store._store["workflows/req-1/tasks/build/error_message"]


def test_prompt_carries_contract_fields_and_upstream_outputs():
    store = _store("backend")
    store.kv_put("workflows/req-1/tasks/build/do", "做事")
    store.kv_put("workflows/req-1/tasks/build/input", "上游产物")
    store.kv_put("workflows/req-1/tasks/build/output", "本步骤产物")
    store.kv_put("workflows/req-1/tasks/build/depends_on", json.dumps(["upstream"]))
    store.kv_put("workflows/req-1/tasks/upstream/output", json.dumps({
        "declared": "上游产物", "evidence": {"path": "up.md"},
    }))
    dispatcher = _dispatcher(store)

    prompt = dispatcher._build_prompt("req-1", "build", {
        "type": "backend", "do": "做事", "input": "上游产物",
        "output": "本步骤产物", "depends_on": ["upstream"],
    })
    package = json.loads(prompt)

    assert set(package) == {"do", "input", "output", "parameters"}
    assert package["do"] == "做事"
    assert package["output"] == "本步骤产物"
    # 上游产物并入 input
    assert "上游产物" in package["input"]
    assert "[上游产物] upstream: 上游产物" in package["input"]
