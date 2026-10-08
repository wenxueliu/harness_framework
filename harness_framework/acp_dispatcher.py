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
from .human_interaction import (
    claim_next_human_message,
    finish_human_message,
    has_pending_interrupt,
    list_human_messages,
)
from .kv_store_protocol import KVStore
from .run_manager import RunManager
from .workspace_manager import WorkspaceManager
from .event_journal import EventJournal
from .project_groups import UNASSIGNED_GROUP_ID
from .api_errors import ConflictError
from .job_flows import INSTANCE_PREFIX

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
WORKFLOW_SCOPE = "workflow"
JOBFLOW_SCOPE = "jobflow"


class WorkspaceUnavailable(RuntimeError):
    pass


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
        workspace_manager: WorkspaceManager | None = None,
        event_journal: EventJournal | None = None,
        scope: str = WORKFLOW_SCOPE,
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
        self.workspace_manager = workspace_manager
        self.event_journal = event_journal or EventJournal(store)
        self.scope = scope
        self.human_messages_supported = scope == WORKFLOW_SCOPE
        if max_concurrency < 1:
            raise ValueError("ACP max_concurrency must be positive")
        if task_timeout < 1 or lease_duration < 1:
            raise ValueError("ACP timeouts must be positive")
        if any(value not in {"claude", "codex"} for value in self.routing.values()):
            raise ValueError("ACP routing values must be claude or codex")
        if scope not in {WORKFLOW_SCOPE, JOBFLOW_SCOPE}:
            raise ValueError("ACP scope must be workflow or jobflow")
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str], dict[str, Any]] = {}

    def _task_base(self, scope_id: str, task_name: str) -> str:
        if self.scope == JOBFLOW_SCOPE:
            return f"{INSTANCE_PREFIX}/{scope_id}/tasks/{task_name}"
        return f"workflows/{scope_id}/tasks/{task_name}"

    def _scope_base(self, scope_id: str) -> str:
        if self.scope == JOBFLOW_SCOPE:
            return f"{INSTANCE_PREFIX}/{scope_id}"
        return f"workflows/{scope_id}"

    def _task_state(self, value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("state", ""))
        return str(value or "")

    def _read_task_status(self, scope_id: str, task_name: str) -> tuple[Any, int]:
        raw, index = self.store.kv_get(f"{self._task_base(scope_id, task_name)}/status")
        if self.scope != JOBFLOW_SCOPE or isinstance(raw, dict):
            return raw, index
        try:
            return json.loads(raw), index
        except (TypeError, json.JSONDecodeError):
            return {}, index

    def _write_task_state(
        self, scope_id: str, task_name: str, state: str, index: int,
        *, current: Any = None, fields: dict[str, Any] | None = None,
    ) -> bool:
        base = self._task_base(scope_id, task_name)
        if self.scope != JOBFLOW_SCOPE:
            return self.store.kv_put(f"{base}/status", state, cas=index)
        status = dict(current) if isinstance(current, dict) else {}
        status.update({"task_id": task_name, "state": state, **(fields or {})})
        status["updated_at"] = _now_iso()
        return self.store.kv_put(
            f"{base}/status", json.dumps(status, ensure_ascii=False), cas=index,
        )

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
            except ConflictError as exc:
                # A scope conflict is an expected scheduler outcome; leave the
                # task PENDING so the next safe slot can claim it.
                log.info("defer %s/%s: %s", req_id, task_name, exc)
                continue
            except (ValueError, ACPError) as exc:
                self.store.kv_put(
                    f"{self._task_base(req_id, task_name)}/dispatch_error", str(exc)
                )
                log.error("cannot dispatch %s/%s: %s", req_id, task_name, exc)
                continue
            if not claim:
                continue
            key = (
                (f"{self.scope}:{req_id}", task_name)
                if self.scope == JOBFLOW_SCOPE else (req_id, task_name)
            )
            with self._lock:
                self._active[key] = {**claim, "scope_id": req_id, "client": None}
            thread = threading.Thread(
                target=self._execute,
                args=(req_id, task_name, meta, claim),
                name=f"acp-{self.scope}-{req_id}-{task_name}",
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
                elif parts[2] == "execution_mode":
                    workflow["execution_mode"] = value
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
            if workflow.get("execution_mode") == "managed-workspace":
                run_id = self.run_manager.get_active_run(req_id)
                if not run_id:
                    continue
                workspace_raw, _ = self.store.kv_get(
                    f"workflows/{req_id}/runs/{run_id}/workspace/record"
                )
                try:
                    workspace_status = json.loads(workspace_raw or "{}").get("status")
                except json.JSONDecodeError:
                    workspace_status = None
                if workspace_status not in {"READY", "ACTIVE"}:
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
        base = self._task_base(req_id, task_name)
        status, index = self._read_task_status(req_id, task_name)
        if self._task_state(status) != "PENDING":
            return None
        if self.scope == JOBFLOW_SCOPE:
            instance_raw, _ = self.store.kv_get(f"{self._scope_base(req_id)}/status")
            try:
                instance_state = json.loads(instance_raw or "{}").get("state", "")
            except (TypeError, json.JSONDecodeError):
                instance_state = ""
            if instance_state != "RUNNING":
                return None
        provider, _config = self._resolve_agent(meta)
        if provider not in self.commands:
            self._mark_unroutable(req_id, task_name, provider)
            return None
        execution_mode, _ = self.store.kv_get(f"{self._scope_base(req_id)}/execution_mode")
        if execution_mode == "managed-workspace":
            run_id = self.run_manager.get_active_run(req_id)
            if not run_id:
                return None
            if self.workspace_manager is None:
                raise ValueError("managed workspace service is not configured")
        else:
            if self.scope == JOBFLOW_SCOPE:
                run_id = self.run_manager.get_active_run(req_id)
                if not run_id:
                    return None
            else:
                run_id = self.run_manager.get_or_create_run(req_id, "acp-dispatcher")
        attempt_id = f"attempt-{uuid.uuid4().hex}"
        previous_epoch, _ = self.store.kv_get(f"{base}/lease_epoch")
        lease_epoch = int(previous_epoch or "0") + 1
        binding_key = ""
        isolated_workspace_id = None
        if execution_mode == "managed-workspace":
            raw_scope = _json_value(meta.get("write_scope"), [])
            if not isinstance(raw_scope, list) or not all(
                isinstance(item, str) for item in raw_scope
            ):
                raise ValueError("write_scope must be a list of strings")
            if str(meta.get("workspace_binding_type", "RUN_SHARED")) == "ISOLATED":
                isolated = self.workspace_manager.provision_isolated_workspace(
                    req_id=req_id, run_id=run_id, task_id=task_name,
                    attempt_id=attempt_id,
                )
                isolated_workspace_id = isolated["workspace_id"]
            self.workspace_manager.bind_attempt(
                req_id=req_id, run_id=run_id, task_id=task_name,
                attempt_id=attempt_id, write_scope=tuple(raw_scope),
                isolated_workspace_id=isolated_workspace_id,
            )
            binding_key = (
                f"workflows/{req_id}/runs/{run_id}/tasks/{task_name}/attempts/"
                f"{attempt_id}/workspace-binding"
            )
        attempt_fields = {}
        if self.scope == JOBFLOW_SCOPE:
            attempt_fields = {
                "current_attempt": attempt_id,
                "attempt_count": int(status.get("attempt_count", 0) or 0) + 1,
            }
        if not self._write_task_state(
            req_id, task_name, "IN_PROGRESS", index,
            current=status, fields=attempt_fields,
        ):
            if binding_key:
                self.store.kv_delete(binding_key)
            if isolated_workspace_id:
                self.workspace_manager.discard_isolated_workspace(isolated_workspace_id)
            return None
        self._task_event(
            req_id, run_id, task_name, attempt_id, "TASK_STATUS_CHANGED",
            {"previous_status": "PENDING", "status": "IN_PROGRESS",
             "provider": provider},
            {"type": "agent", "id": f"acp:{provider}"},
        )
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
        if self.scope == JOBFLOW_SCOPE:
            self.store.kv_put(f"{base}/attempts/{attempt_id}", json.dumps({
                "attempt_id": attempt_id, "state": "IN_PROGRESS",
                "agent_id": agent_id, "provider": provider,
                "created_at": now,
            }, ensure_ascii=False))
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
        key = (
            (f"{self.scope}:{req_id}", task_name)
            if self.scope == JOBFLOW_SCOPE else (req_id, task_name)
        )
        base = self._task_base(req_id, task_name)
        provider = claim["provider"]
        event_count = 0
        session_id = ""
        current_message: dict[str, Any] | None = None

        def on_update(params: dict[str, Any]) -> None:
            nonlocal event_count
            event_count += 1
            event_key = f"{int(time.time() * 1000000):021d}-{event_count:06d}"
            if session_id:
                self.store.kv_put(
                    f"{self._scope_base(req_id)}/sessions/{task_name}/{session_id}/events/{event_key}",
                    json.dumps({
                        "timestamp": _now_iso(), "type": "ACP_UPDATE",
                        "provider": provider, "run_id": claim["run_id"],
                        "attempt_id": claim["attempt_id"], "payload": params,
                    }, ensure_ascii=False),
                )
                self._task_event(
                    req_id, claim["run_id"], task_name, claim["attempt_id"],
                    "SESSION_EVENT", {"session_id": session_id,
                                      "event_count": event_count},
                    {"type": "agent", "id": claim["agent_id"]},
                )
                self.event_journal.append(
                    "SESSION_EVENT",
                    subject={
                        "req_id": req_id, "run_id": claim["run_id"],
                        "task_id": task_name, "attempt_id": claim["attempt_id"],
                    },
                    actor={"type": "agent", "id": claim["agent_id"]},
                    data={"session_id": session_id, "payload": params},
                )
                update = params.get("update", {}) if isinstance(params, dict) else {}
                changed_path = ""
                if isinstance(update, dict):
                    changed_path = str(update.get("path") or update.get("filePath") or update.get("file_path") or "")
                if changed_path:
                    self.event_journal.append(
                        "WORKSPACE_FILE_CHANGED",
                        subject={"req_id": req_id, "run_id": claim["run_id"],
                                 "task_id": task_name, "attempt_id": claim["attempt_id"]},
                        actor={"type": "agent", "id": claim["agent_id"]},
                        data={"path": changed_path, "source": "agent",
                              "session_id": session_id},
                    )

        try:
            provider, config = self._resolve_agent(meta)
            execution_mode, _ = self.store.kv_get(f"{self._scope_base(req_id)}/execution_mode")
            if execution_mode == "managed-workspace":
                try:
                    if self.workspace_manager is None:
                        raise RuntimeError("managed workspace service is not configured")
                    cwd = self.workspace_manager.resolve_attempt_path(
                        req_id, claim["run_id"], task_name, claim["attempt_id"]
                    )
                except Exception as exc:
                    raise WorkspaceUnavailable(str(exc)) from exc
            else:
                if self.scope == JOBFLOW_SCOPE and (config.get("cwd") or meta.get("repo_path")):
                    raise ValueError("jobflow tasks require an approved workspace binding")
                cwd = os.path.abspath(
                    config.get("cwd") or meta.get("repo_path") or self.workspace_root
                )
            client = self.client_factory(
                self.commands[provider], cwd=cwd,
                env={
                    "AGENT_ID": claim["agent_id"], "REQ_ID": req_id,
                    "TASK_NAME": task_name, "ATTEMPT_ID": claim["attempt_id"],
                    "LEASE_EPOCH": str(claim["lease_epoch"]),
                },
                permission_policy=(
                    "deny"
                    if self.permission_policy == "deny"
                    or config.get("permission_policy") == "deny"
                    else self.permission_policy
                ),
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
            pending_human = list_human_messages(
                self.store, req_id, task_name, pending_only=True,
            ) if self.human_messages_supported else []
            resumed_for_human = False
            if not resume_id and pending_human:
                previous_session, _ = self.store.kv_get(f"{base}/acp/session_id")
                if previous_session:
                    resume_id = str(previous_session)
                    resumed_for_human = True
            if resume_id:
                session_id = client.load_session(resume_id)
            else:
                session_id = client.new_session()
            self.store.kv_put(f"{base}/native_session_id", session_id)
            self.store.kv_put(f"{base}/harness_session_id", session_id)
            self.store.kv_put(f"{base}/acp/session_id", session_id)
            self.run_manager.record_session_start(
                req_id, claim["run_id"], task_name, session_id, claim["agent_id"],
                claim["attempt_id"],
            )
            prompt_text = self._build_prompt(req_id, task_name, meta)
            if resumed_for_human:
                current_message = claim_next_human_message(
                    self.store, req_id, task_name,
                ) if self.human_messages_supported else None
                if current_message:
                    prompt_text = self._build_human_prompt(current_message)

            while True:
                result = client.prompt(
                    prompt_text,
                    timeout=self.task_timeout,
                    should_cancel=lambda: (
                        self._should_cancel(req_id, task_name, claim)
                        or (self.human_messages_supported
                            and has_pending_interrupt(self.store, req_id, task_name))
                    ),
                )
                if result.stop_reason not in SUCCESS_STOP_REASONS:
                    interrupted = result.stop_reason in {"cancelled", "canceled"}
                    cancellation_reason = (
                        self._cancel_reason(req_id, task_name, claim)
                        if interrupted else ""
                    )
                    if cancellation_reason in {
                        "PAUSED", "ABORTED", "ABORT", "DISPATCHER_STOPPED",
                    }:
                        self._release_for_control(
                            req_id, task_name, claim, cancellation_reason,
                        )
                        if session_id:
                            self.run_manager.record_session_end(
                                req_id, claim["run_id"], task_name, event_count, 0,
                                "cancelled", f"ACP task cancelled: {cancellation_reason}",
                                claim["attempt_id"],
                            )
                        return
                    if (not interrupted
                            or not (self.human_messages_supported
                                    and has_pending_interrupt(self.store, req_id, task_name))):
                        raise ACPError(
                            f"ACP turn stopped with {result.stop_reason or 'unknown reason'}"
                        )
                    if current_message:
                        finish_human_message(
                            self.store, req_id, task_name, current_message,
                            status="INTERRUPTED",
                            response=_agent_text(result.updates),
                        )
                        current_message = None
                elif current_message:
                    finish_human_message(
                        self.store, req_id, task_name, current_message,
                        status="APPLIED",
                        response=_agent_text(result.updates),
                    )
                    current_message = None

                current_message = claim_next_human_message(
                    self.store, req_id, task_name,
                ) if self.human_messages_supported else None
                if not current_message:
                    break
                prompt_text = self._build_human_prompt(current_message)

            missing = self._missing_completion_requirements(req_id, task_name)
            if missing:
                raise ACPError("completion contract not satisfied: " + ", ".join(missing))
            self._complete(req_id, task_name, claim, result)
            self.run_manager.record_session_end(
                req_id, claim["run_id"], task_name, event_count, 0,
                "completed", f"ACP {provider} turn completed", claim["attempt_id"],
            )
        except WorkspaceUnavailable as exc:
            if current_message:
                finish_human_message(
                    self.store, req_id, task_name, current_message,
                    status="FAILED", error=str(exc),
                )
            self._wait_for_workspace(req_id, task_name, claim, str(exc))
            log.error("ACP task %s/%s waiting for workspace: %s", req_id, task_name, exc)
        except Exception as exc:
            if current_message:
                finish_human_message(
                    self.store, req_id, task_name, current_message,
                    status="FAILED", error=str(exc),
                )
            self._fail(req_id, task_name, claim, str(exc))
            if session_id:
                self.run_manager.record_session_end(
                    req_id, claim["run_id"], task_name, event_count, 1,
                    "error", str(exc), claim["attempt_id"],
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
                f"{self._task_base(req_id, source)}/acp/provider"
            )
            if source_provider and source_provider != provider:
                raise ValueError(
                    "cannot continue an ACP session created by a different provider"
                )
            value, _ = self.store.kv_get(
                f"{self._task_base(req_id, source)}/acp/session_id"
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

    @staticmethod
    def _build_human_prompt(message: dict[str, Any]) -> str:
        return (
            "A human has provided a follow-up instruction for this task. "
            "Apply it in the existing workspace and session, reconcile it with the "
            "task requirements, and run appropriate verification. Do not merely "
            "describe the requested change.\n\nHUMAN MESSAGE:\n"
            + json.dumps({
                "message_id": message.get("message_id", ""),
                "actor": message.get("actor", ""),
                "mode": message.get("mode", "queue"),
                "message": message.get("message", ""),
            }, ensure_ascii=False, indent=2)
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
                key = f"{self._scope_base(req_id)}/context/{selector[7:]}"
            else:
                key = f"{self._scope_base(req_id)}/knowledge/{selector}"

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
        base = self._task_base(req_id, task_name)
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
        base = self._task_base(req_id, task_name)
        if not self._attempt_is_current(base, claim):
            return
        status, index = self._read_task_status(req_id, task_name)
        if self._task_state(status) != "IN_PROGRESS":
            return
        payload = {
            "transport": "acp", "provider": claim["provider"],
            "session_id": result.session_id, "stop_reason": result.stop_reason,
            "agent_text": _agent_text(result.updates), "usage": result.response.get("usage"),
        }
        if not self._write_task_state(
            req_id, task_name, "DONE", index, current=status,
        ):
            return
        self.store.kv_put(f"{base}/validity", "VALID")
        self.store.kv_put(f"{base}/completed_by", claim["agent_id"])
        self.store.kv_put(f"{base}/completed_at", _now_iso())
        self.store.kv_put(f"{base}/result", json.dumps(payload, ensure_ascii=False))
        self.run_manager.record_transition(
            req_id, claim["run_id"], task_name, "IN_PROGRESS", "DONE",
            claim["agent_id"], "ACP turn completed", {"provider": claim["provider"]},
        )
        if self.scope == WORKFLOW_SCOPE:
            self.run_manager.check_run_completion(req_id, claim["run_id"])
        self._task_event(
            req_id, claim["run_id"], task_name, claim["attempt_id"],
            "TASK_STATUS_CHANGED", {"previous_status": "IN_PROGRESS", "status": "DONE"},
            {"type": "agent", "id": claim["agent_id"]},
        )

    def _fail(
        self, req_id: str, task_name: str, claim: dict[str, Any], error: str
    ) -> None:
        base = self._task_base(req_id, task_name)
        if not self._attempt_is_current(base, claim):
            return
        status, index = self._read_task_status(req_id, task_name)
        if self._task_state(status) != "IN_PROGRESS":
            return
        if not self._write_task_state(
            req_id, task_name, "FAILED", index, current=status,
            fields={"error_message": error[:8000]},
        ):
            return
        self.store.kv_put(f"{base}/failed_by", claim["agent_id"])
        self.store.kv_put(f"{base}/failed_at", _now_iso())
        self.store.kv_put(f"{base}/error_message", error[:8000])
        self.run_manager.record_transition(
            req_id, claim["run_id"], task_name, "IN_PROGRESS", "FAILED",
            claim["agent_id"], error[:1000], {"provider": claim["provider"]},
        )
        if self.scope == WORKFLOW_SCOPE:
            self.run_manager.check_run_completion(req_id, claim["run_id"])
        self._task_event(
            req_id, claim["run_id"], task_name, claim["attempt_id"],
            "TASK_STATUS_CHANGED", {"previous_status": "IN_PROGRESS", "status": "FAILED",
                                     "error": error[:1000]},
            {"type": "agent", "id": claim["agent_id"]},
        )

    def _wait_for_workspace(
        self, req_id: str, task_name: str, claim: dict[str, Any], error: str
    ) -> None:
        base = self._task_base(req_id, task_name)
        if not self._attempt_is_current(base, claim):
            return
        status, index = self._read_task_status(req_id, task_name)
        if self._task_state(status) != "IN_PROGRESS":
            return
        if not self._write_task_state(
            req_id, task_name, "WAITING_FOR_HUMAN", index, current=status,
            fields={"waiting_reason": "WORKSPACE_UNAVAILABLE"},
        ):
            return
        self.store.kv_put(f"{base}/waiting_reason", "WORKSPACE_UNAVAILABLE")
        self.store.kv_put(f"{base}/error_message", error[:8000])
        self.run_manager.record_transition(
            req_id, claim["run_id"], task_name, "IN_PROGRESS", "WAITING_FOR_HUMAN",
            claim["agent_id"], error[:1000],
            {"provider": claim["provider"], "reason": "WORKSPACE_UNAVAILABLE"},
        )
        self._task_event(
            req_id, claim["run_id"], task_name, claim["attempt_id"],
            "TASK_STATUS_CHANGED", {"previous_status": "IN_PROGRESS",
                                     "status": "WAITING_FOR_HUMAN",
                                     "reason": "WORKSPACE_UNAVAILABLE"},
            {"type": "system", "id": "acp-dispatcher"},
        )

    def _task_event(
        self, req_id: str, run_id: str, task_id: str, attempt_id: str,
        event_type: str, data: dict[str, Any], actor: dict[str, str],
    ) -> None:
        group_id = ""
        if self.scope == JOBFLOW_SCOPE:
            meta_value, _ = self.store.kv_get(f"{self._scope_base(req_id)}/meta")
            try:
                meta = json.loads(meta_value or "{}")
                template_id = meta.get("template_id", "")
                template_value, _ = self.store.kv_get(
                    f"jobflows/templates/{template_id}/meta"
                )
                group_id = json.loads(template_value or "{}").get("group_id", "")
            except (json.JSONDecodeError, TypeError):
                group_id = ""
        else:
            group_id, _ = self.store.kv_get(f"workflows/{req_id}/project-group")
        try:
            self.event_journal.append(
                event_type,
                subject={"group_id": group_id or UNASSIGNED_GROUP_ID,
                         "req_id": req_id, "run_id": run_id,
                         "task_id": task_id, "attempt_id": attempt_id},
                actor=actor, data=data,
            )
        except Exception:
            # Event delivery must not roll back or strand an already-CASed task;
            # the KV transition remains the execution source of truth.
            log.exception("failed to journal task event req=%s task=%s", req_id, task_id)

    def _mark_unroutable(self, req_id: str, task_name: str, provider: str) -> None:
        base = self._task_base(req_id, task_name)
        self.store.kv_put(f"{base}/dispatch_error", f"ACP command not configured: {provider}")

    def _attempt_is_current(self, base: str, claim: dict[str, Any]) -> bool:
        attempt, _ = self.store.kv_get(f"{base}/attempt_id")
        epoch, _ = self.store.kv_get(f"{base}/lease_epoch")
        return attempt == claim["attempt_id"] and str(epoch) == str(claim["lease_epoch"])

    def _should_cancel(
        self, req_id: str, task_name: str, claim: dict[str, Any]
    ) -> bool:
        return bool(self._cancel_reason(req_id, task_name, claim))

    def _cancel_reason(
        self, req_id: str, task_name: str, claim: dict[str, Any]
    ) -> str:
        if self._stop.is_set():
            return "DISPATCHER_STOPPED"
        if self.scope == JOBFLOW_SCOPE:
            status_raw, _ = self.store.kv_get(f"{self._scope_base(req_id)}/status")
            try:
                instance_state = json.loads(status_raw or "{}").get("state", "")
            except (TypeError, json.JSONDecodeError):
                instance_state = ""
            if instance_state in {"PAUSED", "ABORTED"}:
                return instance_state
        else:
            control, _ = self.store.kv_get(f"{self._scope_base(req_id)}/control")
            if control == "ABORT":
                return "ABORT"
        if not self._attempt_is_current(
            self._task_base(req_id, task_name), claim
        ):
            return "FENCED"
        return ""

    def _release_for_control(
        self, req_id: str, task_name: str, claim: dict[str, Any], reason: str
    ) -> None:
        base = self._task_base(req_id, task_name)
        if not self._attempt_is_current(base, claim):
            return
        status, index = self._read_task_status(req_id, task_name)
        if self._task_state(status) != "IN_PROGRESS":
            return
        new_state = "ABORTED" if reason in {"ABORTED", "ABORT"} else "PENDING"
        if not self._write_task_state(req_id, task_name, new_state, index, current=status):
            return
        self.store.kv_put(f"{base}/attempt_id", "")
        if new_state == "ABORTED":
            self.store.kv_put(f"{base}/aborted_at", _now_iso())
        self.run_manager.record_transition(
            req_id, claim["run_id"], task_name, "IN_PROGRESS", new_state,
            claim["agent_id"], f"ACP control cancellation: {reason}",
            {"provider": claim["provider"], "reason": reason},
        )
        self._task_event(
            req_id, claim["run_id"], task_name, claim["attempt_id"],
            "TASK_STATUS_CHANGED",
            {"previous_status": "IN_PROGRESS", "status": new_state, "reason": reason},
            {"type": "system", "id": "acp-dispatcher"},
        )

    def _maintain_active(self) -> None:
        with self._lock:
            entries = list(self._active.items())
        for (_active_id, task_name), claim in entries:
            req_id = claim["scope_id"]
            base = self._task_base(req_id, task_name)
            if not self._attempt_is_current(base, claim):
                client = claim.get("client")
                if client:
                    client.cancel()
                continue
            now = _now_iso()
            self.store.kv_put(f"{base}/lease_renewed_at", now)
            self.store.kv_put(f"{base}/lease_expires_at", _deadline(self.lease_duration))


class JobFlowACPDispatcher(ACPDispatcher):
    """Dispatch PENDING tasks for the new Template/Version/Instance model."""

    def __init__(
        self,
        store: KVStore,
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
        event_journal: EventJournal | None = None,
    ):
        super().__init__(
            store,
            RunManager(store, event_journal=event_journal or EventJournal(store),
                       instance_mode=True),
            commands=commands,
            routing=routing,
            workspace_root=workspace_root,
            poll_interval=poll_interval,
            task_timeout=task_timeout,
            lease_duration=lease_duration,
            max_concurrency=max_concurrency,
            permission_policy=permission_policy,
            client_factory=client_factory,
            event_journal=event_journal,
            scope=JOBFLOW_SCOPE,
        )

    def _tick(self) -> None:
        super()._tick()
        self._recover_expired_tasks()

    def _pending_tasks(self) -> list[tuple[str, str, dict[str, str]]]:
        items, _ = self.store.kv_get(f"{INSTANCE_PREFIX}/", recurse=True)
        if not items:
            return []
        instances: dict[str, dict[str, Any]] = {}
        for item in items:
            parts = item["Key"].split("/")
            if len(parts) < 3 or parts[:2] != ["jobflows", "instances"]:
                continue
            instance_id = parts[2]
            instance = instances.setdefault(instance_id, {"tasks": {}})
            if len(parts) == 4 and item["Key"] == f"{INSTANCE_PREFIX}/{instance_id}/status":
                try:
                    status = json.loads(item.get("_decoded", "{}"))
                except (TypeError, json.JSONDecodeError):
                    status = {}
                if isinstance(status, dict):
                    instance["status"] = status
            elif len(parts) >= 5 and parts[3] == "tasks" and item["Key"].endswith("/status"):
                task_id = parts[4]
                try:
                    status = json.loads(item.get("_decoded", "{}"))
                except (TypeError, json.JSONDecodeError):
                    status = {}
                if isinstance(status, dict):
                    instance["tasks"].setdefault(task_id, {}).update(status)
            elif len(parts) >= 6 and parts[3] == "tasks" and parts[5] == "definition":
                try:
                    definition = json.loads(item.get("_decoded", "{}"))
                except (TypeError, json.JSONDecodeError):
                    definition = {}
                if isinstance(definition, dict):
                    instance["tasks"].setdefault(parts[4], {}).update(definition)

        pending: list[tuple[str, str, dict[str, str]]] = []
        for instance_id, instance in sorted(instances.items()):
            if instance.get("status", {}).get("state") != "RUNNING":
                continue
            for task_id, meta in instance["tasks"].items():
                if meta.get("state") != "PENDING":
                    continue
                if meta.get("type") in {"parallel", "aggregate"}:
                    continue
                normalized = dict(meta)
                if isinstance(normalized.get("acp"), dict):
                    normalized["acp"] = json.dumps(normalized["acp"], ensure_ascii=False)
                pending.append((instance_id, task_id, normalized))
        return sorted(pending, key=lambda item: (item[0], item[1]))

    def _recover_expired_tasks(self) -> None:
        now = _now_iso()
        for instance_id, task_id, meta in self._jobflow_task_meta():
            if meta.get("state") != "IN_PROGRESS":
                continue
            base = self._task_base(instance_id, task_id)
            hard_deadline_raw, _ = self.store.kv_get(f"{base}/hard_deadline_at")
            lease_deadline_raw, _ = self.store.kv_get(f"{base}/lease_expires_at")
            hard_deadline = str(hard_deadline_raw or "")
            lease_deadline = str(lease_deadline_raw or "")
            if hard_deadline and hard_deadline <= now:
                status, index = self._read_task_status(instance_id, task_id)
                self._write_task_state(
                    instance_id, task_id, "FAILED", index, current=status,
                    fields={"error_message": "hard deadline exceeded"},
                )
                self.store.kv_put(f"{base}/failed_at", now)
                self.store.kv_put(f"{base}/error_message", "hard deadline exceeded")
                continue
            if not lease_deadline or lease_deadline > now:
                continue
            status, index = self._read_task_status(instance_id, task_id)
            epoch, _ = self.store.kv_get(f"{base}/lease_epoch")
            self._write_task_state(
                instance_id, task_id, "PENDING", index, current=status,
                fields={"current_attempt": "", "lease_epoch": int(epoch or "0") + 1},
            )
            self.store.kv_put(f"{base}/lease_epoch", str(int(epoch or "0") + 1))

    def _jobflow_task_meta(self) -> list[tuple[str, str, dict[str, Any]]]:
        items, _ = self.store.kv_get(f"{INSTANCE_PREFIX}/", recurse=True)
        if not items:
            return []
        running: set[str] = set()
        tasks: list[tuple[str, str, dict[str, Any]]] = []
        for item in items:
            key = item.get("Key", "")
            parts = key.split("/")
            if len(parts) < 4 or parts[:2] != ["jobflows", "instances"]:
                continue
            instance_id = parts[2]
            if len(parts) == 4 and key.endswith("/status"):
                try:
                    if json.loads(item.get("_decoded", "{}")).get("state") == "RUNNING":
                        running.add(instance_id)
                except (TypeError, json.JSONDecodeError):
                    pass
            elif len(parts) >= 6 and parts[3] == "tasks" and key.endswith("/status"):
                try:
                    meta = json.loads(item.get("_decoded", "{}"))
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(meta, dict):
                    tasks.append((instance_id, parts[4], meta))
        return [
            (instance_id, task_id, meta)
            for instance_id, task_id, meta in tasks
            if instance_id in running
        ]


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
