"""Authentication context and capability-based authorization for WebAPI."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional
from urllib.parse import quote

from .kv_store_protocol import KVStore


class AuthenticationError(RuntimeError):
    """Raised when a request cannot establish a trusted identity."""


class Role(str, Enum):
    OWNER = "OWNER"
    MAINTAINER = "MAINTAINER"
    DEVELOPER = "DEVELOPER"
    VIEWER = "VIEWER"


ROLE_CAPABILITIES: dict[Role, frozenset[str]] = {
    Role.OWNER: frozenset({
        "group:read", "group:manage", "member:manage", "group:archive",
        "workspace:register", "workspace:policy", "workspace:path:diagnose",
        "workflow:draft", "workflow:publish", "run:create", "run:control",
        "task:retry", "task:message:queue", "task:message:interrupt",
        "file:read", "file:write", "checkpoint:create",
    }),
    Role.MAINTAINER: frozenset({
        "group:read", "workflow:draft", "workflow:publish", "run:create",
        "run:control", "task:retry", "task:message:queue",
        "task:message:interrupt", "file:read", "file:write",
        "checkpoint:create", "workspace:path:diagnose",
    }),
    Role.DEVELOPER: frozenset({
        "group:read", "workflow:draft", "task:message:queue", "file:read",
        "file:write", "checkpoint:create",
    }),
    Role.VIEWER: frozenset({"group:read", "file:read"}),
}


def member_key_segment(subject: str) -> str:
    """Encode an external subject so it cannot alter the KV key hierarchy."""
    return quote(subject, safe="")


@dataclass(frozen=True)
class AuthenticationContext:
    subject: str
    display_name: str
    mode: str
    claims: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthConfig:
    mode: str = "local"
    local_user: str = "local-user"
    trusted_proxy_addresses: frozenset[str] = frozenset({"127.0.0.1", "::1"})
    platform_owners: frozenset[str] = frozenset()
    subject_header: str = "X-Harness-Subject"
    display_name_header: str = "X-Harness-Display-Name"

    def __post_init__(self) -> None:
        if self.mode not in {"local", "trusted-proxy"}:
            raise ValueError("auth mode must be local or trusted-proxy")
        if self.mode == "local" and not self.local_user.strip():
            raise ValueError("local user must not be empty")


def authenticate_request(
    config: AuthConfig,
    headers: Mapping[str, str],
    peer_address: str,
) -> AuthenticationContext:
    """Resolve identity only from an explicitly configured trust boundary."""
    if config.mode == "local":
        return AuthenticationContext(
            subject=f"local:{config.local_user}",
            display_name=config.local_user,
            mode="local",
        )

    if peer_address not in config.trusted_proxy_addresses:
        raise AuthenticationError("request did not originate from a trusted proxy")
    subject = (headers.get(config.subject_header) or "").strip()
    if not subject:
        raise AuthenticationError("trusted proxy did not provide a subject")
    display_name = (headers.get(config.display_name_header) or subject).strip()
    return AuthenticationContext(
        subject=subject,
        display_name=display_name,
        mode="trusted-proxy",
    )


class AuthorizationService:
    """Resolve project-group roles and effective capabilities."""

    def __init__(self, store: KVStore, auth_config: AuthConfig):
        self.store = store
        self.auth_config = auth_config

    def role_for(
        self, context: AuthenticationContext, group_id: Optional[str] = None
    ) -> Optional[Role]:
        # Local mode is deliberately a clearly-labelled single-user owner mode.
        if context.mode == "local":
            return Role.OWNER
        if context.subject in self.auth_config.platform_owners:
            return Role.OWNER
        if not group_id:
            return None
        raw, _ = self.store.kv_get(
            f"project-groups/{group_id}/members/{member_key_segment(context.subject)}"
        )
        # Read pre-encoding records during migration when the old key could not
        # escape the members prefix.
        if not raw and "/" not in context.subject:
            raw, _ = self.store.kv_get(
                f"project-groups/{group_id}/members/{context.subject}"
            )
        if not raw:
            return None
        try:
            record = json.loads(raw)
            return Role(str(record.get("role", "")).upper())
        except (json.JSONDecodeError, ValueError, AttributeError):
            return None

    def capabilities_for(
        self, context: AuthenticationContext, group_id: Optional[str] = None
    ) -> frozenset[str]:
        role = self.role_for(context, group_id)
        return ROLE_CAPABILITIES.get(role, frozenset()) if role else frozenset()

    def require(
        self,
        context: AuthenticationContext,
        capability: str,
        group_id: Optional[str] = None,
    ) -> None:
        if capability not in self.capabilities_for(context, group_id):
            raise PermissionError(f"missing capability: {capability}")
