from __future__ import annotations

import json

import pytest

from harness_framework.auth import (
    AuthConfig,
    AuthenticationError,
    AuthorizationService,
    Role,
    resolve_role,
    authenticate_request,
)
from harness_framework.local_store import LocalStore


def test_local_mode_uses_configured_user_and_owner_capabilities():
    config = AuthConfig(mode="local", local_user="alice")
    context = authenticate_request(config, {}, "203.0.113.10")
    authorization = AuthorizationService(LocalStore(), config)

    assert context.subject == "local:alice"
    assert authorization.role_for(context, "any-group") is Role.ADMIN
    assert "group:manage" in authorization.capabilities_for(context, "any-group")


def test_trusted_proxy_rejects_untrusted_peer_and_missing_subject():
    config = AuthConfig(
        mode="trusted-proxy", trusted_proxy_addresses=frozenset({"10.0.0.2"})
    )
    with pytest.raises(AuthenticationError, match="trusted proxy"):
        authenticate_request(config, {"X-Harness-Subject": "user:alice"}, "10.0.0.3")
    with pytest.raises(AuthenticationError, match="subject"):
        authenticate_request(config, {}, "10.0.0.2")


def test_group_membership_controls_capabilities():
    store = LocalStore()
    store.kv_put("project-groups/g1/members/user:alice", json.dumps({
        "subject_id": "user:alice", "role": "DEVELOPER"
    }))
    config = AuthConfig(
        mode="trusted-proxy", trusted_proxy_addresses=frozenset({"10.0.0.2"})
    )
    context = authenticate_request(
        config, {"X-Harness-Subject": "user:alice"}, "10.0.0.2"
    )
    authorization = AuthorizationService(store, config)

    assert authorization.role_for(context, "g1") is Role.EDITOR
    assert "file:write" in authorization.capabilities_for(context, "g1")
    assert "run:create" in authorization.capabilities_for(context, "g1")


def test_legacy_role_mapping():
    assert resolve_role("OWNER") is Role.ADMIN
    assert resolve_role("MAINTAINER") is Role.EDITOR
    assert resolve_role("DEVELOPER") is Role.EDITOR
    assert resolve_role("VIEWER") is Role.VIEWER
    assert resolve_role("ADMIN") is Role.ADMIN
    assert resolve_role("EDITOR") is Role.EDITOR


def test_editor_cannot_access_admin_capabilities():
    store = LocalStore()
    store.kv_put("project-groups/g1/members/user:bob", json.dumps({
        "subject_id": "user:bob", "role": "MAINTAINER"
    }))
    config = AuthConfig(
        mode="trusted-proxy", trusted_proxy_addresses=frozenset({"10.0.0.2"})
    )
    context = authenticate_request(
        config, {"X-Harness-Subject": "user:bob"}, "10.0.0.2"
    )
    authorization = AuthorizationService(store, config)
    assert authorization.role_for(context, "g1") is Role.EDITOR
    assert "profile:manage" not in authorization.capabilities_for(context, "g1")
    assert "workflow:draft" in authorization.capabilities_for(context, "g1")
