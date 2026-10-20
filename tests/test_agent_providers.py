"""Tests for the supported ACP agent provider registry and its error messages."""
from __future__ import annotations

import json

import pytest

from harness_framework.acp_dispatcher import ACPDispatcher
from harness_framework.agent_providers import (
    SUPPORTED_AGENT_PROVIDERS,
    unsupported_agent_message,
)
from harness_framework.job_flows import JobFlowService
from harness_framework.run_manager import RunManager
from tests.conftest import MockConsulStore


def test_opencode_is_a_supported_provider():
    assert "opencode" in SUPPORTED_AGENT_PROVIDERS
    assert set(SUPPORTED_AGENT_PROVIDERS) == {"claude", "codex", "opencode"}


def test_unsupported_message_names_agent_and_allowed_values():
    message = unsupported_agent_message("gemini", node="build")
    assert "build" in message
    assert "gemini" in message
    assert "claude, codex, opencode" in message


def test_dispatcher_routes_opencode_task(tmp_path):
    store = MockConsulStore({
        "workflows/req-1/published": "true",
        "workflows/req-1/status": "IN_PROGRESS",
        "workflows/req-1/tasks/build/status": "PENDING",
        "workflows/req-1/tasks/build/type": "backend",
        "workflows/req-1/tasks/build/acp": json.dumps({"agent": "opencode"}),
    })
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"],
                  "opencode": ["opencode", "acp"]},
    )
    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    claim = dispatcher._claim(req_id, task_name, meta)
    assert claim is not None
    assert claim["provider"] == "opencode"


def test_dispatcher_rejects_unknown_provider_with_specific_name():
    store = MockConsulStore({
        "workflows/req-1/published": "true",
        "workflows/req-1/status": "IN_PROGRESS",
        "workflows/req-1/tasks/build/status": "PENDING",
        "workflows/req-1/tasks/build/type": "backend",
        "workflows/req-1/tasks/build/acp": json.dumps({"agent": "gemini"}),
    })
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"],
                  "opencode": ["opencode", "acp"]},
    )
    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    with pytest.raises(ValueError) as excinfo:
        dispatcher._claim(req_id, task_name, meta)
    message = str(excinfo.value)
    assert "gemini" in message
    assert "claude, codex, opencode" in message


def test_unroutable_provider_logs_specific_name(caplog):
    store = MockConsulStore({
        "workflows/req-1/published": "true",
        "workflows/req-1/status": "IN_PROGRESS",
        "workflows/req-1/tasks/build/status": "PENDING",
        "workflows/req-1/tasks/build/type": "backend",
        "workflows/req-1/tasks/build/acp": json.dumps({"agent": "opencode"}),
    })
    # opencode is a valid provider but the daemon was not given a command for it
    dispatcher = ACPDispatcher(
        store, RunManager(store),
        commands={"claude": ["claude-acp"], "codex": ["codex-acp"]},
    )
    req_id, task_name, meta = dispatcher._pending_tasks()[0]
    with caplog.at_level("ERROR", logger="acp_dispatcher"):
        assert dispatcher._claim(req_id, task_name, meta) is None
    assert "opencode" in caplog.text
    dispatch_error = store._store[
        "workflows/req-1/tasks/build/dispatch_error"
    ]
    assert "opencode" in dispatch_error


def test_job_flow_validation_reports_offending_agent():
    store = MockConsulStore()
    service = JobFlowService(store)
    from harness_framework.api_errors import APIError

    with pytest.raises(APIError) as excinfo:
        service._manifest({
            "tasks": [
                {"id": "build", "type": "backend", "agent": "gemini",
                 "depends_on": []},
            ],
        })
    error = excinfo.value
    assert error.code == "INVALID_AGENT"
    assert "gemini" in error.message
    assert "claude, codex, opencode" in error.message
    assert error.details["supported_agents"] == list(SUPPORTED_AGENT_PROVIDERS)
    assert error.details["location"]["node"] == "build"
