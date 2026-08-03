from __future__ import annotations

import pytest

from harness_framework.model_execution import resolve_execution, validate_execution


PROFILE = {
    "codex": {
        "provider": "codex",
        "command": ["codex-wrapper"],
        "args": ["--json"],
        "model": "default-model",
        "model_args": ["--model", "{model}"],
        "resume_session_args": ["--resume", "{session_id}"],
    }
}


def test_new_profile_builds_command_with_model_override():
    resolved = resolve_execution(
        {"profile": "codex", "model": "fast", "session": {"mode": "new"}},
        PROFILE, lambda _task: None, allowed_executables={"codex-wrapper"},
    )
    assert resolved.command == ["codex-wrapper", "--json", "--model", "fast"]
    assert resolved.session_mode == "new"


def test_continue_resolves_source_task_native_session():
    resolved = resolve_execution(
        {"profile": "codex", "session": {"mode": "continue", "from_task": "build"}},
        PROFILE, lambda task: "native-123" if task == "build" else None,
        allowed_executables={"codex-wrapper"},
    )
    assert resolved.native_session_id == "native-123"
    assert resolved.command[-2:] == ["--resume", "native-123"]


def test_resume_requires_session_id():
    with pytest.raises(ValueError, match="session_id is required"):
        validate_execution({"profile": "codex", "session": {"mode": "resume"}})


def test_direct_command_must_be_allowlisted():
    with pytest.raises(ValueError, match="not allowed"):
        resolve_execution(
            {"command": ["sh"], "session": {"mode": "new"}}, {},
            lambda _task: None, allowed_executables={"codex-wrapper"},
        )


def test_continue_fails_when_source_has_no_session():
    with pytest.raises(ValueError, match="has no native session"):
        resolve_execution(
            {"profile": "codex", "session": {"mode": "continue", "from_task": "build"}},
            PROFILE, lambda _task: None,
        )
