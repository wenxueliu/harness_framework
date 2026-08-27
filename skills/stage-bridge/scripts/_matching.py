"""Canonical Agent-name matching."""
from __future__ import annotations

from typing import Any, Mapping


def task_target_name(metadata: Mapping[str, Any]) -> str:
    """Return the explicit logical Agent Name targeted by a task."""
    value = metadata.get("agent_name") or ""
    return str(value).strip()


def task_matches_agent(metadata: Mapping[str, Any], agent_name: str) -> bool:
    """Require an explicit task target that equals the registered Agent Name."""
    target = task_target_name(metadata)
    return bool(target and agent_name and target == agent_name.strip())
