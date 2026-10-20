"""Supported ACP agent providers.

Single source of truth for which providers the daemon can dispatch to and what
the default adapter launch command is.  Every validation error must name the
offending provider and the allowed set so operators can locate the misconfigured
task from the log alone.
"""
from __future__ import annotations

from typing import Any

#: Providers the ACP dispatcher can route to, in display order.
SUPPORTED_AGENT_PROVIDERS: tuple[str, ...] = ("claude", "codex", "opencode")

#: Provider used when a task type is not present in the routing table.
DEFAULT_AGENT_PROVIDER = "codex"

#: Default adapter argv per provider. ``opencode`` ships its own ACP server, so
#: it needs no npx download; the other two are the official Zed adapters.
DEFAULT_AGENT_COMMANDS: dict[str, list[str]] = {
    "claude": ["npx", "-y", "@agentclientprotocol/claude-agent-acp"],
    "codex": ["npx", "-y", "@agentclientprotocol/codex-acp"],
    "opencode": ["opencode", "acp"],
}


def supported_agents_text() -> str:
    """Comma-separated provider list used inside error messages."""
    return ", ".join(SUPPORTED_AGENT_PROVIDERS)


def normalize_agent(agent: Any) -> str:
    """Canonical provider name: trimmed, lower-cased; non-strings become ``""``."""
    return agent.strip().lower() if isinstance(agent, str) else ""


def is_supported_agent(agent: Any) -> bool:
    return normalize_agent(agent) in SUPPORTED_AGENT_PROVIDERS


def unsupported_agent_message(agent: Any, *, node: str = "") -> str:
    """Message that always names the rejected provider and the allowed set."""
    shown = agent if isinstance(agent, str) and agent else repr(agent)
    prefix = f"节点 {node} 的 Agent " if node else "Agent "
    return f"{prefix}不受支持: {shown}（可选: {supported_agents_text()}）"
