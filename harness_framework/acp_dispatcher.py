"""Push-based task dispatcher backed by Agent Client Protocol (ACP)."""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Callable

from .acp_client import ACPClient, ACPError, ACPResult
from .kv_store_protocol import KVStore
from .run_manager import RunManager

log = logging.getLogger("acp_dispatcher")

DEFAULT_AGENT_ROUTING = {
    "design": "claude",
    "review": "claude",
    "backend": "codex",
    "frontend": "codex",
    "test": "codex",
    "deploy": "codex",
    "task": "codex",
    "generic": "codex",
}
SUCCESS_STOP_REASONS = frozenset({"end_turn"})


class ACPDispatcher:
    """Create an ACP agent when a DAG task reaches PENDING."""

    def __init__(
        self,
        store: KVStore,
        run_manager: RunManager,
        *,
        commands: dict[str, list[str]],
        routing: dict[str, str] | None = None,
        workspace_root: str = "",
        poll_interval: float = 1,
        task_timeout: int = 7200,
        lease_duration: int = 120,
        max_concurrency: int = 4,
        permission_policy: str = "allow_once",
        client_factory: Callable[..., ACPClient] = ACPClient,
    ):
        self.store = store
        self.run_manager = run_manager
        self.commands = {key: list(value) for key, value in commands.items()}
        self.routing = {**DEFAULT_AGENT_ROUTING, **(routing or {})}
        self.workspace_root = os.path.abspath(workspace_root or os.getcwd())
        self.poll_interval = poll_interval
        self.task_timeout = task_timeout
        self.lease_duration = lease_duration
        self.max_concurrency = max_concurrency
        self.permission_policy = permission_policy
        self.client_factory = client_factory
        if max_concurrency < 1:
            raise ValueError("ACP max_concurrency must be positive")
        if task_timeout < 1 or lease_duration < 1:
            raise ValueError("ACP timeouts must be positive")
        if any(value not in {"claude", "codex"} for value in self.routing.values()):
            raise ValueError("ACP routing values must be claude or codex")
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str], dict[str, Any]] = {}

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            clients = [entry.get("client") for entry in self._active.values()]
        for client in clients:
            if client:
                try:
                    client.cancel()
                except ACPError:
                    pass

    def run(self) -> None:
        log.info("ACP dispatcher started, routing=%s", self.routing)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                log.exception("ACP dispatcher tick failed: %s", exc)
            self._stop.wait(self.poll_interval)

    def _tick(self) -> None:
        self._maintain_active()
        with self._lock:
            slots = self.max_concurrency - len(self._active)
        if slots <= 0:
            return
        for req_id, task_name, meta in self._pending_tasks():
            if slots <= 0 or self._stop.is_set():
                break
            try:
                claim = self._claim(req_id, task_name, meta)
            except (ValueError, ACPError) as exc:
                self.store.kv_put(
                    f"workflows/{req_id}/tasks/{task_name}/dispatch_error", str(exc)
                )
                log.error("cannot dispatch %s/%s: %s", req_id, task_name, exc)
                continue
            if not claim:
                continue
            key = (req_id, task_name)
            with self._lock:
                self._active[key] = {**claim, "client": None}
            thread = threading.Thread(
                target=self._execute,
                args=(req_id, task_name, meta, claim),
                name=f"acp-{req_id}-{task_name}",
                daemon=True,
            )
            with self._lock:
                self._active[key]["thread"] = thread
            thread.start()
            slots -= 1

    def _pending_tasks(self) -> list[tuple[str, str, dict[str, str]]]:
        items, _ = self.store.kv_get("workflows/", recurse=True)
        if not items:
            return []
        workflows: dict[str, dict[str, Any]] = {}
        for item in items:
            parts = item["Key"].split("/")
            if len(parts) < 3 or parts[0] != "workflows":
                continue
            req_id = parts[1]
            workflow = workflows.setdefault(req_id, {"tasks": {}, "priority": 0})
            value = item.get("_decoded", "")
            if len(parts) == 3:
                if parts[2] == "published":
                    workflow["published"] = value == "true"
                elif parts[2] == "control":
                    workflow["control"] = value
                elif parts[2] == "status":
                    workflow["status"] = value
                elif parts[2] == "priority":
                    try:
                        workflow["priority"] = int(value)
                    except ValueError:
                        pass
            elif len(parts) >= 5 and parts[2] == "tasks":
                workflow["tasks"].setdefault(parts[3], {})["/".join(parts[4:])] = value

        pending = []
        for req_id, workflow in workflows.items():
            if not workflow.get("published"):
                continue
            if workflow.get("control") in {"PAUSE", "ABORT"}:
                continue
            if workflow.get("status") == "Proposal":
                continue
            for task_name, meta in workflow["tasks"].items():
                if meta.get("status") != "PENDING":
                    continue
                if meta.get("type") in {"parallel", "aggregate"}:
                    continue
                pending.append((req_id, task_name, meta, workflow["priority"]))
        pending.sort(key=lambda item: (-item[3], item[0], item[1]))
        return [(req_id, name, meta) for req_id, name, meta, _priority in pending]

    def _claim(self, req_id: str, task_name: str, meta: dict[str, str]) -> dict[str, Any] | None:
        base = f"workflows/{req_id}/tasks/{task_name}"
        status, index = self.store.kv_get(f"{base}/status")
        if status != "PENDING":
            return None
        provider, _config = self._resolve_agent(meta)
        if provider not in self.commands:
            self._mark_unroutable(req_id, task_name, provider)
            return None
        if not self.store.kv_put(f"{base}/status", "IN_PROGRESS", cas=index):
            return None
        attempt_id = f"attempt-{uuid.uuid4().hex}"
        previous_epoch, _ = self.store.kv_get(f"{base}/lease_epoch")
        lease_epoch = int(previous_epoch or "0") + 1
        agent_id = f"acp:{provider}:{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        self.store.kv_put(f"{base}/attempt_id", attempt_id)
        self.store.kv_put(f"{base}/lease_epoch", str(lease_epoch))
        self.store.kv_put(f"{base}/assigned_agent", agent_id)
        self.store.kv_put(f"{base}/execution_transport", "acp")
        self.store.kv_put(f"{base}/acp/provider", provider)
        self.store.kv_put(f"{base}/started_at", now)
        self.store.kv_put(f"{base}/lease_renewed_at", now)
        self.store.kv_put(f"{base}/lease_expires_at", _deadline(self.lease_duration))
        self.store.kv_put(f"{base}/hard_deadline_at", _deadline(self.task_timeout))
        run_id = self.run_manager.get_or_create_run(req_id, "acp-dispatcher")
        self.run_manager.record_transition(
            req_id, run_id, task_name, "PENDING", "IN_PROGRESS", agent_id,
            "dispatched through ACP", {"provider": provider, "attempt_id": attempt_id},
        )
        return {
            "provider": provider, "attempt_id": attempt_id,
            "lease_epoch": lease_epoch, "agent_id": agent_id, "run_id": run_id,
        }

    def _execute(
        self, req_id: str, task_name: str, meta: dict[str, str], claim: dict[str, Any]
    ) -> None:
        key = (req_id, task_name)
        base = f"workflows/{req_id}/tasks/{task_name}"
        provider = claim["provider"]
        event_count = 0
        session_id = ""

        def on_update(params: dict[str, Any]) -> None:
            nonlocal event_count
            event_count += 1
            event_key = f"{int(time.time() * 1000000):021d}-{event_count:06d}"
            if session_id:
                self.store.kv_put(
                    f"workflows/{req_id}/sessions/{task_name}/{session_id}/events/{event_key}",
                    json.dumps({
                        "timestamp": _now_iso(), "type": "ACP_UPDATE",
                        "provider": provider, "payload": params,
                    }, ensure_ascii=False),
                )

        try:
            provider, config = self._resolve_agent(meta)
            cwd = os.path.abspath(config.get("cwd") or meta.get("repo_path") or self.workspace_root)
            client = self.client_factory(
                self.commands[provider], cwd=cwd,
                env={
                    "AGENT_ID": claim["agent_id"], "REQ_ID": req_id,
                    "TASK_NAME": task_name, "ATTEMPT_ID": claim["attempt_id"],
                    "LEASE_EPOCH": str(claim["lease_epoch"]),
                },
                permission_policy=config.get("permission_policy", self.permission_policy),
                update_handler=on_update,
            )
            with self._lock:
                if key in self._active:
                    self._active[key]["client"] = client
            client.start()
            initialized = client.initialize()
            self.store.kv_put(
                f"{base}/acp/initialize",
                json.dumps(initialized, ensure_ascii=False),
            )
            resume_id = self._session_to_resume(req_id, config, provider)
            if resume_id:
                session_id = client.load_session(resume_id)
            else:
                session_id = client.new_session()
            self.store.kv_put(f"{base}/native_session_id", session_id)
            self.store.kv_put(f"{base}/harness_session_id", session_id)
            self.store.kv_put(f"{base}/acp/session_id", session_id)
            self.run_manager.record_session_start(
                req_id, claim["run_id"], task_name, session_id, claim["agent_id"]
            )
            result = client.prompt(
                self._build_prompt(req_id, task_name, meta),
                timeout=self.task_timeout,
                should_cancel=lambda: self._should_cancel(req_id, task_name, claim),
            )
            if result.stop_reason not in SUCCESS_STOP_REASONS:
                raise ACPError(f"ACP turn stopped with {result.stop_reason or 'unknown reason'}")
            missing = self._missing_completion_requirements(req_id, task_name)
            if missing:
                raise ACPError("completion contract not satisfied: " + ", ".join(missing))
            self._complete(req_id, task_name, claim, result)
            self.run_manager.record_session_end(
                req_id, claim["run_id"], task_name, event_count, 0,
                "completed", f"ACP {provider} turn completed",
            )
        except Exception as exc:
            self._fail(req_id, task_name, claim, str(exc))
            if session_id:
                self.run_manager.record_session_end(
                    req_id, claim["run_id"], task_name, event_count, 1,
                    "error", str(exc),
                )
            log.error("ACP task %s/%s failed: %s", req_id, task_name, exc)
        finally:
            with self._lock:
                entry = self._active.pop(key, None)
            client = entry.get("client") if entry else None
            if client:
                client.close()

    def _resolve_agent(self, meta: dict[str, str]) -> tuple[str, dict[str, Any]]:
        raw = meta.get("acp", "")
        try:
            config = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise ValueError("task acp configuration is invalid JSON") from exc
        if not isinstance(config, dict):
            raise ValueError("task acp configuration must be an object")
        task_type = meta.get("type", "task")
        provider = config.get("agent") or self.routing.get(task_type, "codex")
        if provider not in {"claude", "codex"}:
            raise ValueError(f"unsupported ACP agent: {provider}")
        return provider, config

    def _session_to_resume(
        self, req_id: str, config: dict[str, Any], provider: str
    ) -> str:
        session = config.get("session", {})
        if not isinstance(session, dict):
            raise ValueError("acp.session must be an object")
        mode = session.get("mode", "new")
        if mode == "new":
            return ""
        if mode == "resume":
            value = session.get("session_id", "")
        elif mode == "continue":
            source = session.get("from_task", "")
            source_provider, _ = self.store.kv_get(
                f"workflows/{req_id}/tasks/{source}/acp/provider"
            )
            if source_provider and source_provider != provider:
                raise ValueError(
                    "cannot continue an ACP session created by a different provider"
                )
            value, _ = self.store.kv_get(
                f"workflows/{req_id}/tasks/{source}/acp/session_id"
            )
        else:
            raise ValueError(f"unsupported acp.session.mode: {mode}")
        if not value:
            raise ValueError(f"ACP {mode} session could not be resolved")
        return str(value)

    def _build_prompt(self, req_id: str, task_name: str, meta: dict[str, str]) -> str:
        context = self._load_context(req_id, task_name, meta)
        contract = _json_value(meta.get("agent_contract"), {})
        completion = _json_value(meta.get("completion_contract"), {})
        package = {
            "workflow_id": req_id,
            "task_name": task_name,
            "task_type": meta.get("type", "task"),
            "description": meta.get("description", ""),
            "service_name": meta.get("service_name", ""),
            "agent_contract": contract,
            "completion_contract": completion,
            "context": context,
        }
        return (
            "You are the execution agent for one Harness Framework DAG task. "
            "Work autonomously in the provided workspace, implement the task completely, "
            "run appropriate verification, and do not merely describe what should be done. "
            "Respect the contract and exclusions. If completion artifacts or evidence gates "
            "are required, record them with the installed stage-bridge commands before ending. "
            "Do not claim success when verification fails.\n\nTASK PACKAGE:\n"
            + json.dumps(package, ensure_ascii=False, indent=2)
        )

    def _load_context(
        self, req_id: str, task_name: str, meta: dict[str, str]
    ) -> dict[str, str]:
        selectors = _json_value(meta.get("context_inputs"), [])
        if not isinstance(selectors, list):
            raise ValueError("context_inputs must be a list")
        result: dict[str, str] = {}
        for selector in selectors:
            if not isinstance(selector, str):
                raise ValueError("context_inputs must be a list of strings")
            if selector.startswith(("restricted/", "events/")):
                raise ValueError(f"invalid ACP context selector: {selector}")
            if selector.startswith("working_memory/"):
                allowed = f"working_memory/{task_name}/"
                if not selector.startswith(allowed):
                    raise ValueError("task cannot inject another task's working memory")
            namespace = selector.split("/", 1)[0]
            if namespace not in {
                "facts", "artifacts", "summaries", "working_memory", "legacy"
            }:
                raise ValueError(f"unknown context_inputs namespace: {namespace}")
            if namespace == "legacy":
                key = f"workflows/{req_id}/context/{selector[7:]}"
            else:
                key = f"workflows/{req_id}/knowledge/{selector}"

            if selector.endswith("/*"):
                prefix = key[:-1]
                items, _ = self.store.kv_get(prefix, recurse=True)
                for item in items or []:
                    result_key = selector[:-1] + item["Key"][len(prefix):]
                    result[result_key] = item.get("_decoded", "")
                continue

            if namespace == "artifacts":
                pointer, _ = self.store.kv_get(f"{key}/current")
                if not pointer:
                    continue
                try:
                    version_id = json.loads(pointer)["version_id"]
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    raise ValueError(
                        f"invalid context artifact pointer: {selector}"
                    ) from exc
                value, _ = self.store.kv_get(f"{key}/versions/{version_id}/value")
            else:
                value, _ = self.store.kv_get(key)
            if value is not None:
                result[selector] = value
        return result

    def _missing_completion_requirements(self, req_id: str, task_name: str) -> list[str]:
        base = f"workflows/{req_id}/tasks/{task_name}"
        raw, _ = self.store.kv_get(f"{base}/completion_contract")
        contract = _json_value(raw, {})
        missing = []
        for artifact in contract.get("required_artifacts", []):
            value, _ = self.store.kv_get(f"{base}/artifacts/{artifact}/current_version")
            if not value:
                missing.append(f"artifact:{artifact}")
        for gate in contract.get("required_gates", []):
            value, _ = self.store.kv_get(f"{base}/evidence/{gate}/verdict")
            if value != "PASS":
                missing.append(f"gate:{gate}")
        return missing

    def _complete(
        self, req_id: str, task_name: str, claim: dict[str, Any], result: ACPResult
    ) -> None:
        base = f"workflows/{req_id}/tasks/{task_name}"
        if not self._attempt_is_current(base, claim):
            return
        status, index = self.store.kv_get(f"{base}/status")
        if status != "IN_PROGRESS":
            return
        payload = {
            "transport": "acp", "provider": claim["provider"],
            "session_id": result.session_id, "stop_reason": result.stop_reason,
            "agent_text": _agent_text(result.updates), "usage": result.response.get("usage"),
        }
        if not self.store.kv_put(f"{base}/status", "DONE", cas=index):
            return
        self.store.kv_put(f"{base}/validity", "VALID")
        self.store.kv_put(f"{base}/completed_by", claim["agent_id"])
        self.store.kv_put(f"{base}/completed_at", _now_iso())
        self.store.kv_put(f"{base}/result", json.dumps(payload, ensure_ascii=False))
        self.run_manager.record_transition(
            req_id, claim["run_id"], task_name, "IN_PROGRESS", "DONE",
            claim["agent_id"], "ACP turn completed", {"provider": claim["provider"]},
        )
        self.run_manager.check_run_completion(req_id, claim["run_id"])

    def _fail(
        self, req_id: str, task_name: str, claim: dict[str, Any], error: str
    ) -> None:
        base = f"workflows/{req_id}/tasks/{task_name}"
        if not self._attempt_is_current(base, claim):
            return
        status, index = self.store.kv_get(f"{base}/status")
        if status != "IN_PROGRESS":
            return
        if not self.store.kv_put(f"{base}/status", "FAILED", cas=index):
            return
        self.store.kv_put(f"{base}/failed_by", claim["agent_id"])
        self.store.kv_put(f"{base}/failed_at", _now_iso())
        self.store.kv_put(f"{base}/error_message", error[:8000])
        self.run_manager.record_transition(
            req_id, claim["run_id"], task_name, "IN_PROGRESS", "FAILED",
            claim["agent_id"], error[:1000], {"provider": claim["provider"]},
        )
        self.run_manager.check_run_completion(req_id, claim["run_id"])

    def _mark_unroutable(self, req_id: str, task_name: str, provider: str) -> None:
        base = f"workflows/{req_id}/tasks/{task_name}"
        self.store.kv_put(f"{base}/dispatch_error", f"ACP command not configured: {provider}")

    def _attempt_is_current(self, base: str, claim: dict[str, Any]) -> bool:
        attempt, _ = self.store.kv_get(f"{base}/attempt_id")
        epoch, _ = self.store.kv_get(f"{base}/lease_epoch")
        return attempt == claim["attempt_id"] and str(epoch) == str(claim["lease_epoch"])

    def _should_cancel(
        self, req_id: str, task_name: str, claim: dict[str, Any]
    ) -> bool:
        if self._stop.is_set():
            return True
        control, _ = self.store.kv_get(f"workflows/{req_id}/control")
        if control == "ABORT":
            return True
        return not self._attempt_is_current(
            f"workflows/{req_id}/tasks/{task_name}", claim
        )

    def _maintain_active(self) -> None:
        with self._lock:
            entries = list(self._active.items())
        for (req_id, task_name), claim in entries:
            base = f"workflows/{req_id}/tasks/{task_name}"
            if not self._attempt_is_current(base, claim):
                client = claim.get("client")
                if client:
                    client.cancel()
                continue
            now = _now_iso()
            self.store.kv_put(f"{base}/lease_renewed_at", now)
            self.store.kv_put(f"{base}/lease_expires_at", _deadline(self.lease_duration))


def _json_value(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON task metadata") from exc
    return value


def _agent_text(updates: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for params in updates:
        update = params.get("update", {})
        content = update.get("content")
        if isinstance(content, dict) and content.get("type") == "text":
            chunks.append(str(content.get("text", "")))
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    chunks.append(str(item.get("text", "")))
    return "".join(chunks)[-20000:]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _deadline(seconds: int) -> str:
    value = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    return value.isoformat().replace("+00:00", "Z")
