"""Task-scoped model command and native-session policy resolution."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


SESSION_MODES = {"new", "continue", "resume"}


@dataclass(frozen=True)
class ResolvedExecution:
    provider: str
    model: str
    command: list[str]
    session_mode: str
    native_session_id: str = ""
    source_task: str = ""
    profile: str = ""


def _string_list(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if value is None and allow_empty:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"execution.{field} must be a list of non-empty strings")
    if not value and not allow_empty:
        raise ValueError(f"execution.{field} must not be empty")
    return list(value)


def validate_execution(execution: Any) -> None:
    """Validate a task execution declaration without resolving a profile."""
    if execution is None:
        return
    if not isinstance(execution, dict):
        raise ValueError("execution must be an object")
    if not execution:
        raise ValueError("execution must not be empty")
    profile = execution.get("profile", "")
    if profile and not isinstance(profile, str):
        raise ValueError("execution.profile must be a string")
    if "command" in execution:
        _string_list(execution["command"], "command", allow_empty=False)
    for field in ("args", "model_args", "new_session_args", "resume_session_args"):
        if field in execution:
            _string_list(execution[field], field)
    for field in ("provider", "model"):
        if field in execution and not isinstance(execution[field], str):
            raise ValueError(f"execution.{field} must be a string")
    session = execution.get("session", {"mode": "new"})
    if not isinstance(session, dict):
        raise ValueError("execution.session must be an object")
    mode = session.get("mode", "new")
    if mode not in SESSION_MODES:
        raise ValueError(f"execution.session.mode must be one of {sorted(SESSION_MODES)}")
    if mode == "continue" and not session.get("from_task"):
        raise ValueError("execution.session.from_task is required for continue mode")
    if mode == "resume" and not session.get("session_id"):
        raise ValueError("execution.session.session_id is required for resume mode")
    if mode == "new" and (session.get("from_task") or session.get("session_id")):
        raise ValueError("new session mode cannot specify from_task or session_id")
    if not profile and "command" not in execution:
        raise ValueError("execution requires profile or command")


def load_execution_profiles(path: str) -> dict[str, dict]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    profiles = data.get("execution_profiles", data)
    if not isinstance(profiles, dict):
        raise ValueError("execution profiles file must contain an object")
    for name, profile in profiles.items():
        if not isinstance(name, str) or not isinstance(profile, dict):
            raise ValueError("each execution profile must be an object")
        validate_execution({**profile, "session": {"mode": "new"}})
    return profiles


def _format_args(values: list[str], *, model: str, session_id: str) -> list[str]:
    try:
        return [value.format(model=model, session_id=session_id) for value in values]
    except KeyError as exc:
        raise ValueError(f"unknown execution argument placeholder: {exc.args[0]}") from exc


def resolve_execution(
    execution: dict,
    profiles: dict[str, dict],
    session_lookup: Callable[[str], str | None],
    *,
    allowed_executables: set[str] | None = None,
) -> ResolvedExecution:
    """Resolve a task declaration to a safe argv and native session."""
    validate_execution(execution)
    profile_name = execution.get("profile", "")
    if profile_name:
        if profile_name not in profiles:
            raise ValueError(f"unknown execution profile: {profile_name}")
        merged = {**profiles[profile_name], **execution}
    else:
        merged = dict(execution)

    command = _string_list(merged.get("command"), "command", allow_empty=False)
    executable = os.path.basename(command[0])
    if allowed_executables is not None and executable not in allowed_executables:
        raise ValueError(f"execution command is not allowed: {executable}")

    session = merged.get("session", {"mode": "new"})
    mode = session.get("mode", "new")
    source_task = session.get("from_task", "")
    native_session_id = session.get("session_id", "")
    if mode == "continue":
        native_session_id = session_lookup(source_task) or ""
        if not native_session_id:
            raise ValueError(f"source task has no native session: {source_task}")

    model = merged.get("model", "")
    argv = command + _format_args(
        _string_list(merged.get("args"), "args"), model=model,
        session_id=native_session_id,
    )
    if model:
        argv += _format_args(
            _string_list(merged.get("model_args", ["--model", "{model}"]), "model_args"),
            model=model, session_id=native_session_id,
        )
    policy_args = (
        merged.get("new_session_args", []) if mode == "new"
        else merged.get("resume_session_args")
    )
    if policy_args is None:
        raise ValueError("execution profile does not support session resume")
    argv += _format_args(
        _string_list(policy_args, "resume_session_args" if mode != "new" else "new_session_args"),
        model=model, session_id=native_session_id,
    )
    return ResolvedExecution(
        provider=merged.get("provider", "custom"), model=model, command=argv,
        session_mode=mode, native_session_id=native_session_id,
        source_task=source_task, profile=profile_name,
    )
