"""Deployment feature and actor capability negotiation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from .auth import AuthenticationContext, AuthorizationService


KNOWN_FEATURES = (
    "project_groups",
    "workspace_browse",
    "workspace_write",
    "workspace_diff",
    "sse_events",
)


@dataclass(frozen=True)
class FeatureConfig:
    values: Mapping[str, bool] = field(default_factory=dict)

    def enabled(self, name: str) -> bool:
        return bool(self.values.get(name, False))


class CapabilitiesService:
    def __init__(
        self,
        authorization: AuthorizationService,
        features: Optional[FeatureConfig] = None,
    ):
        self.authorization = authorization
        self.features = features or FeatureConfig()

    def snapshot(
        self,
        context: AuthenticationContext,
        *,
        group_id: Optional[str] = None,
        req_id: Optional[str] = None,
        run_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
    ) -> dict:
        feature_values = {
            name: self.features.enabled(name) for name in KNOWN_FEATURES
        }
        reasons = {
            name: "此部署尚未启用该能力"
            for name, enabled in feature_values.items()
            if not enabled
        }
        permissions = self.authorization.capabilities_for(context, group_id, req_id)
        permission_requirements = {
            "workspace_browse": "file:read",
            "workspace_diff": "file:read",
            "workspace_write": "file:write",
        }
        for feature, required in permission_requirements.items():
            if feature_values[feature] and group_id and required not in permissions:
                feature_values[feature] = False
                reasons[feature] = f"当前项目组缺少 {required} capability"
        return {
            "features": feature_values,
            "permissions": sorted(permissions),
            "mode": context.mode,
            "actor": {
                "subject": context.subject,
                "display_name": context.display_name,
            },
            "reasons": reasons,
            "scope": {
                "group_id": group_id, "req_id": req_id,
                "run_id": run_id, "attempt_id": attempt_id,
            },
        }
