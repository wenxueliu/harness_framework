"""Task-level Capability Preflight orchestration."""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from .kv_store_protocol import KVStore
from .secrets import SecretStore

def _now_iso(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

JOBFLOW_INSTANCE_PREFIX = "jobflows/instances"

class PreflightError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code; self.message = message; self.status = status
        super().__init__(f"{code}: {message}")

class CapabilityPreflightService:
    CHECK_ORDER = ["agent-runtime", "execution-profile", "skill-bundle", "mcp-grant", "workspace", "budget"]

    def __init__(self, store: KVStore, runtime_service, profile_service, skill_service, mcp_service, workspace_manager, manifest_service):
        self.store = store
        self.runtime_service = runtime_service
        self.profile_service = profile_service
        self.skill_service = skill_service
        self.mcp_service = mcp_service
        self.workspace_manager = workspace_manager
        self.manifest_service = manifest_service

    def _read(self, key: str, default=None):
        raw, _ = self.store.kv_get(key)
        return json.loads(raw) if raw else default

    def _write(self, key: str, value):
        self.store.kv_put(key, json.dumps(value, ensure_ascii=False))

    def _required_runtimes(self, instance: dict, profile: dict) -> list[tuple[str, str]]:
        """解析实例 DAG 节点实际引用的 provider 及其 Runtime ID（去重保序）。

        节点的 agent 字段是 provider 名（claude/codex/opencode），对应 daemon 自动注册的
        acp-<provider> Runtime；旧模板没有 agent 字段时回退到 Profile 绑定的 Runtime。
        """
        template_id = str(instance.get("template_id", ""))
        version_id = str(instance.get("version_id", ""))
        manifest: dict = {}
        if template_id and version_id:
            manifest = self._read(
                f"jobflows/templates/{template_id}/versions/{version_id}/manifest", {}
            ) or {}
        providers: list[str] = []
        for task in (manifest.get("tasks") or []):
            if isinstance(task, dict):
                agent = str(task.get("agent", "")).strip().lower()
                if agent and agent not in providers:
                    providers.append(agent)
        if providers:
            return [(provider, f"acp-{provider}") for provider in providers]
        runtime_id = str(profile.get("agent_runtime_id", "")).strip()
        return [(runtime_id, runtime_id)] if runtime_id else []

    def run(self, instance_id: str) -> dict:
        instance = self._read(f"{JOBFLOW_INSTANCE_PREFIX}/{instance_id}/meta")
        storage_prefix = JOBFLOW_INSTANCE_PREFIX
        if isinstance(instance, dict):
            context = self._read(f"{JOBFLOW_INSTANCE_PREFIX}/{instance_id}/context", {})
            instance = {**instance, "context": context}
        else:
            storage_prefix = "instances"
            instance = self._read(f"{storage_prefix}/{instance_id}")
        if not instance:
            raise PreflightError("INSTANCE_NOT_FOUND", f"Instance {instance_id} 不存在", 404)

        checks = []
        profile_id = instance.get("execution_profile_id", "")
        profile = None

        # Agent Runtime check — 实例 DAG 每个节点可绑定不同 provider（claude/codex/opencode），
        # 因此按节点实际引用的 provider 逐一检查对应 Runtime，而不是只看 Profile 绑定的那一个。
        try:
            if not profile_id:
                raise PreflightError("PROFILE_REQUIRED", "实例未绑定 Execution Profile")
            profile = self.profile_service.get(profile_id)
            for provider, runtime_id in self._required_runtimes(instance, profile):
                try:
                    runtime = self.runtime_service.get(runtime_id)
                    status = "PASS" if runtime["status"] == "HEALTHY" else "FAIL"
                    checks.append({"check_id": f"agent-runtime:{runtime_id}", "status": status,
                                   "detail": f"节点 provider={provider} → {runtime['name']} v{runtime['version']} {runtime['status']}",
                                   **({"reason": f"Runtime {runtime['status']}", "remediation": "检查 Agent Runtime 或联系 Admin"} if status == "FAIL" else {})})
                except Exception as exc:
                    checks.append({"check_id": f"agent-runtime:{runtime_id}", "status": "FAIL",
                                   "detail": f"节点 provider={provider}: {exc}", "reason": str(exc),
                                   "remediation": "检查 Runtime 配置或 daemon 是否已注册该 adapter"})
        except (PreflightError, Exception) as exc:
            checks.append({"check_id": "agent-runtime", "status": "FAIL",
                           "detail": str(exc), "reason": str(exc), "remediation": "检查 Runtime 配置"})

        # Profile check
        if profile:
            checks.append({"check_id": "execution-profile", "status": "PASS" if profile.get("status") == "ACTIVE" else "FAIL",
                           "detail": f"{profile['name']} v{profile['version']} {profile['status']}"})
        else:
            checks.append({"check_id": "execution-profile", "status": "FAIL", "detail": "Profile 不可用",
                           "reason": "Profile 缺失或无效", "remediation": "联系 Admin 配置 Profile"})

        # Skill Bundle check
        if profile:
            skill_ids = profile.get("skill_bundle_ids", [])
            if skill_ids:
                try:
                    skills = [self.skill_service.get(sid) for sid in skill_ids]
                    checks.append({"check_id": "skill-bundle", "status": "PASS",
                                   "detail": ", ".join(f"{s['name']} v{s['version']}" for s in skills)})
                except Exception as exc:
                    checks.append({"check_id": "skill-bundle", "status": "FAIL", "detail": str(exc),
                                   "reason": "Skill Bundle 不可用", "remediation": "检查 Bundle 配置"})
            else:
                checks.append({"check_id": "skill-bundle", "status": "PASS", "detail": "无 Skill Bundle"})

        # MCP Grant check
        if profile:
            grant_ids = profile.get("mcp_grant_ids", [])
            if grant_ids:
                try:
                    grants = [self.mcp_service.get_grant(profile["group_id"], gid) for gid in grant_ids]
                    active = [g for g in grants if g["status"] == "ACTIVE"]
                    if len(active) == len(grants):
                        checks.append({"check_id": "mcp-grant", "status": "PASS", "detail": f"{len(active)} 个 Grant"})
                    else:
                        checks.append({"check_id": "mcp-grant", "status": "FAIL",
                                       "detail": f"{len(active)}/{len(grants)} 个 Grant 有效",
                                       "reason": "部分 MCP Grant 已撤销", "remediation": "联系 Admin 重新授权"})
                except Exception as exc:
                    checks.append({"check_id": "mcp-grant", "status": "FAIL", "detail": str(exc),
                                   "reason": str(exc), "remediation": "检查 MCP 配置"})
            else:
                checks.append({"check_id": "mcp-grant", "status": "PASS", "detail": "无 MCP Grant"})

        # Workspace check
        workspace_id = instance.get("run_workspace_id", "")
        if not workspace_id:
            context = instance.get("context") if isinstance(instance.get("context"), dict) else {}
            workspace = context.get("workspace") if isinstance(context.get("workspace"), dict) else {}
            workspace_id = workspace.get("workspace_id", "")
        if workspace_id:
            checks.append({"check_id": "workspace", "status": "PASS", "detail": f"Run Workspace {workspace_id}"})
        else:
            checks.append({"check_id": "workspace", "status": "PASS", "detail": "无独立 Workspace"})

        # Budget check
        if profile:
            budget = profile.get("resource_budget", {})
            max_cost = budget.get("max_cost_usd")
            if max_cost is not None:
                checks.append({"check_id": "budget", "status": "PASS", "detail": f"预算上限 ${max_cost}"})
            else:
                checks.append({"check_id": "budget", "status": "PASS", "detail": "无预算限制"})

        all_passed = all(c["status"] == "PASS" for c in checks)
        result = {
            "instance_id": instance_id,
            "status": "PASSED" if all_passed else "BLOCKED",
            "checks": checks,
            "manifest_id": None,
            "checked_at": _now_iso(),
        }

        if all_passed and profile:
            attempt_id = instance.get("current_attempt_id", f"attempt-{instance_id}")
            try:
                manifest = self.manifest_service.generate(
                    attempt_id=attempt_id,
                    agent_runtime=self.runtime_service.get(profile["agent_runtime_id"]),
                    execution_profile=profile,
                    skill_bundles=[self.skill_service.get(sid) for sid in profile.get("skill_bundle_ids", [])],
                    mcp_grants=[self.mcp_service.get_grant(profile["group_id"], gid) for gid in profile.get("mcp_grant_ids", [])],
                    workspace_binding={"workspace_id": workspace_id} if workspace_id else {},
                    secret_refs=SecretStore(self.store).references(instance_id),
                )
                result["manifest_id"] = manifest["manifest_id"]
            except Exception as exc:
                try:
                    existing = self.manifest_service.get(attempt_id)
                except Exception:
                    raise PreflightError("MANIFEST_FAILED", str(exc), 503) from exc
                result["manifest_id"] = existing["manifest_id"]

        result["attempt_id"] = instance.get("current_attempt_id", f"attempt-{instance_id}")
        self._write(f"{storage_prefix}/{instance_id}/preflight/latest", result)
        return result
