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
        "file:read", "file:write", "checkpoint:create", "workspace:merge",
    }),
    Role.MAINTAINER: frozenset({
        "group:read", "workflow:draft", "workflow:publish", "run:create",
        "run:control", "task:retry", "task:message:queue",
        "task:message:interrupt", "file:read", "file:write",
        "checkpoint:create", "workspace:path:diagnose", "workspace:merge",
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
        self, context: AuthenticationContext, group_id: Optional[str] = None,
        req_id: Optional[str] = None,
    ) -> frozenset[str]:
        primary_group = None
        if req_id:
            primary_group, _ = self.store.kv_get(f"workflows/{req_id}/project-group")
        effective_group = primary_group or group_id
        role = self.role_for(context, effective_group)
        if req_id and primary_group and group_id and group_id != primary_group:
            role = self.role_for(context, primary_group)
            if not role:
                capabilities = self._reference_capabilities(context, primary_group, req_id)
            else:
                capabilities = ROLE_CAPABILITIES.get(role, frozenset())
        else:
            capabilities = None
        if capabilities is None:
            capabilities = (
                ROLE_CAPABILITIES.get(role, frozenset())
                if role else self._reference_capabilities(context, effective_group, req_id)
            )
        if not capabilities:
            return frozenset()
        policy_group = effective_group or group_id
        if policy_group and policy_group != "unassigned":
            raw, _ = self.store.kv_get(f"project-groups/{policy_group}/record")
            try:
                policy = json.loads(raw).get("policy", {}) if raw else {}
            except (TypeError, json.JSONDecodeError):
                policy = {}
            allowed = policy.get("allowed_capabilities")
            if isinstance(allowed, (list, tuple, set)):
                capabilities = capabilities.intersection(str(item) for item in allowed)
        return frozenset(capabilities)

    def _reference_capabilities(
        self, context: AuthenticationContext, primary_group_id: Optional[str],
        req_id: Optional[str],
    ) -> frozenset[str]:
        """Return role ∩ explicit cross-group reference grants for a workflow."""
        if not req_id or not primary_group_id:
            return frozenset()
        items: list[dict] = []
        cursor = None
        while True:
            page, cursor = self.store.kv_list("project-groups/", cursor=cursor, limit=1000)
            items.extend(page)
            if cursor is None:
                break
        result: set[str] = set()
        for item in items:
            key = item.get("key", "")
            if not key.endswith(f"/references/{req_id}"):
                continue
            parts = key.split("/")
            if len(parts) < 4 or parts[1] == primary_group_id:
                continue
            reference_group = parts[1]
            role = self.role_for(context, reference_group)
            if not role:
                continue
            try:
                record = json.loads(item.get("value", "{}"))
            except (TypeError, json.JSONDecodeError):
                continue
            grants = set(record.get("capabilities", []))
            if "workflow:read" in grants:
                grants.add("group:read")
            result.update(ROLE_CAPABILITIES[role].intersection(grants))
        return frozenset(result)

    def require(
        self,
        context: AuthenticationContext,
        capability: str,
        group_id: Optional[str] = None,
        req_id: Optional[str] = None,
    ) -> None:
        if capability not in self.capabilities_for(context, group_id, req_id):
            raise PermissionError(f"missing capability: {capability}")
