"""Evidence-driven control for long-running DAG tasks.

This module adds a dynamic execution layer without turning the dependency DAG
into a cyclic graph.  The DAG continues to define scheduling and impact
closure; this service owns atomic task actions, evidence, recovery decisions,
human input, and boundary priority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime
import hashlib
import json
import uuid
from typing import Any

from .incremental import affected_downstream_closure, invalidate_impacted_tasks
from .kv_store_protocol import KVStore
from .run_manager import RunManager


VALIDITY_STATES = frozenset({"VALID", "STALE", "INVALIDATED", "UNKNOWN"})
FEEDBACK_STATES = frozenset({"DELIVERED", "OBSERVED", "ACKNOWLEDGED", "APPLIED"})
FEEDBACK_DECISIONS = frozenset({"CONTINUE", "ASK", "PAUSE"})
ACTION_TYPES = frozenset({
    "EXECUTE", "VERIFY", "ROUTE", "INTERPRET_FEEDBACK", "AWAIT_HUMAN",
})


class AdaptiveControlError(RuntimeError):
    """A stable, API-safe adaptive-control rejection."""

    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class RoutingBudget:
    max_total_routes: int = 8
    max_same_edge_routes: int = 2
    max_same_failure_fingerprint: int = 2

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "RoutingBudget":
        value = value or {}
        if not isinstance(value, dict):
            raise ValueError("routing_budget must be an object")
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("unknown routing_budget fields: " + ", ".join(sorted(unknown)))
        for name, candidate in value.items():
            if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 1:
                raise ValueError(f"routing_budget.{name} must be a positive integer")
        return cls(**value)


@dataclass(frozen=True)
class ActionReceipt:
    action_id: str
    action_type: str
    req_id: str
    run_id: str
    task: str
    attempt_id: str
    state_version: int
    actor: str
    issued_at: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdaptiveControlService:
    """Coordinate evidence-based actions and recovery inside a Harness DAG."""

    def __init__(self, store: KVStore, run_manager: RunManager | None = None):
        self.store = store
        self.runs = run_manager or RunManager(store)

    # -- Boundary and action protocol ---------------------------------

    def boundary(self, req_id: str, task: str) -> dict[str, Any]:
        """Return the highest-priority control condition at a safe boundary."""
        base = self._task_base(req_id, task)
        task_control, _ = self.store.kv_get(f"{base}/control")
        workflow_control, _ = self.store.kv_get(f"workflows/{req_id}/control")
        signal = str(task_control or workflow_control or "").upper()
        if signal == "ABORT":
            return {"blocked": True, "kind": "ABORT", "priority": 1}
        if signal == "PAUSE":
            return {"blocked": True, "kind": "PAUSE", "priority": 2}

        question = self._read_json(f"{base}/human/question/current")
        if question and question.get("status") == "OPEN":
            return {"blocked": True, "kind": "AWAIT_HUMAN", "priority": 3,
                    "question": question}
        task_status, _ = self.store.kv_get(f"{base}/status")
        if task_status == "WAITING_FOR_HUMAN":
            return {"blocked": True, "kind": "AWAIT_HUMAN", "priority": 3,
                    "reason": "task requires human intervention"}

        feedback = self.list_feedback(req_id, task, unresolved_only=True)
        if feedback:
            return {"blocked": True, "kind": "FEEDBACK", "priority": 4,
                    "feedback": feedback[0]}

        pending_route = self._read_json(f"{base}/routing/pending")
        if pending_route:
            return {"blocked": True, "kind": "ROUTE", "priority": 5,
                    "check": pending_route,
                    "allowed_targets": self.allowed_recovery_targets(req_id, task)}
        return {"blocked": False, "kind": "ACTIVE", "priority": 6}

    def next_action(self, req_id: str, task: str, *, actor: str,
                    action_type: str = "EXECUTE", attempt_id: str = "",
                    payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a boundary action or issue one fenced business action."""
        boundary = self.boundary(req_id, task)
        if boundary["kind"] == "FEEDBACK":
            item = boundary["feedback"]
            if item["status"] == "DELIVERED":
                item["status"] = "OBSERVED"
                item["observed_at"] = _now_iso()
                self._write_feedback(req_id, task, item)
                self.record_event(req_id, task, "FEEDBACK_OBSERVED", actor,
                                  {"feedback_id": item["feedback_id"]})
            return {**boundary, "type": "INTERPRET_FEEDBACK", "feedback": item,
                    "allowed_decisions": sorted(FEEDBACK_DECISIONS)}
        if boundary["kind"] == "ROUTE":
            return {**boundary, "type": "ROUTE"}
        if boundary["kind"] == "AWAIT_HUMAN":
            return {**boundary, "type": "AWAIT_HUMAN"}
        if boundary["blocked"]:
            return boundary

        action_type = action_type.upper()
        if action_type not in ACTION_TYPES - {"ROUTE", "INTERPRET_FEEDBACK", "AWAIT_HUMAN"}:
            raise AdaptiveControlError("E_INVALID_ACTION", f"unsupported action type: {action_type}")
        base = self._task_base(req_id, task)
        current = self._read_json(f"{base}/actions/current")
        if current and current.get("status") == "ISSUED":
            if current.get("actor") != actor or (attempt_id and current.get("attempt_id") != attempt_id):
                raise AdaptiveControlError("E_ACTION_OWNED", "an action is already issued", current)
            return current

        owned_attempt, _ = self.store.kv_get(f"{base}/attempt_id")
        if owned_attempt and attempt_id != owned_attempt:
            raise AdaptiveControlError(
                "E_STALE_ATTEMPT", "action attempt does not own the task",
                {"expected": owned_attempt, "actual": attempt_id},
            )
        run_id = self.runs.get_or_create_run(req_id, actor)
        lock_key = f"{base}/actions/issue_lock"
        lock_id = uuid.uuid4().hex
        if not self.store.kv_put(lock_key, lock_id, cas=0):
            raise AdaptiveControlError("E_CONCURRENT_UPDATE", "another action is being issued")
        try:
            if self._read_json(f"{base}/actions/current"):
                raise AdaptiveControlError("E_CONCURRENT_UPDATE", "another action is already current")
            state_version = self._increment_version(base)
            receipt = ActionReceipt(
                action_id=f"act-{uuid.uuid4().hex}", action_type=action_type,
                req_id=req_id, run_id=run_id, task=task,
                attempt_id=attempt_id or f"attempt-{uuid.uuid4().hex}",
                state_version=state_version, actor=_required(actor, "actor"),
                issued_at=_now_iso(), payload=payload or {},
            ).to_dict()
            receipt["status"] = "ISSUED"
            if not self.store.kv_put(f"{base}/actions/current", _json(receipt), cas=0):
                raise AdaptiveControlError("E_CONCURRENT_UPDATE", "another action won issuance")
        finally:
            current_lock, _ = self.store.kv_get(lock_key)
            if current_lock == lock_id:
                self.store.kv_delete(lock_key)
        self.store.kv_put(f"{base}/actions/history/{receipt['action_id']}", _json(receipt))
        self.record_event(req_id, task, "ACTION_ISSUED", actor, receipt,
                          correlation_id=receipt["action_id"])
        return receipt

    def submit_check(self, req_id: str, task: str, *, action_id: str,
                     state_version: int, verdict: str, verifier: str,
                     actor: str, evidence: dict[str, Any],
                     command: dict[str, Any] | None = None,
                     artifact_refs: list[str] | None = None,
                     workspace_revision: str = "") -> dict[str, Any]:
        """Consume the current action and persist fresh verifier evidence."""
        self._ensure_business_allowed(req_id, task)
        base = self._task_base(req_id, task)
        action, action_index = self._require_action(base, action_id, state_version)
        verdict = verdict.upper()
        if verdict not in {"PASS", "FAIL", "ERROR"}:
            raise AdaptiveControlError("E_INVALID_VERDICT", "verdict must be PASS, FAIL, or ERROR")
        verifier = _required(verifier, "verifier")
        if not isinstance(evidence, dict):
            raise AdaptiveControlError("E_INVALID_EVIDENCE", "evidence must be an object")
        if command is not None:
            self._validate_command(command)
        if not isinstance(artifact_refs or [], list) or not all(
                isinstance(item, str) and item.strip() for item in artifact_refs or []):
            raise AdaptiveControlError("E_INVALID_EVIDENCE", "artifact_refs must contain strings")

        consuming = dict(action)
        consuming["status"] = "CONSUMING"
        if not self.store.kv_put(
            f"{base}/actions/current", _json(consuming), cas=action_index
        ):
            raise AdaptiveControlError("E_STALE_ACTION", "action was consumed concurrently")

        check = {
            "check_id": f"check-{uuid.uuid4().hex}", "action_id": action_id,
            "attempt_id": action["attempt_id"], "task": task, "verdict": verdict,
            "verifier": verifier, "actor": _required(actor, "actor"),
            "observed_at": _now_iso(), "workspace_revision": workspace_revision,
            "command": command, "evidence": evidence,
            "artifact_refs": artifact_refs or [],
        }
        self.store.kv_put(f"{base}/evidence/{check['check_id']}", _json(check))
        action["status"] = "CONSUMED"
        action["consumed_at"] = check["observed_at"]
        self.store.kv_put(f"{base}/actions/history/{action_id}", _json(action))
        self.store.kv_delete(f"{base}/actions/current")
        self._increment_version(base)
        validity = "VALID" if verdict == "PASS" else "INVALIDATED"
        self.store.kv_put(f"{base}/validity", validity)
        self.store.kv_put(f"{base}/routing/pending", _json(check))
        self.record_event(req_id, task, "CHECK_RECORDED", actor, check,
                          causation_id=action_id, correlation_id=check["check_id"])
        return check

    # -- Dynamic route --------------------------------------------------

    def allowed_recovery_targets(self, req_id: str, current_task: str) -> list[str]:
        dag = self._load_dag(req_id)
        if current_task not in dag:
            raise AdaptiveControlError("E_UNKNOWN_TASK", f"unknown task: {current_task}")
        ancestors = self._ancestors(dag, current_task)
        visited = {
            task for task in ancestors
            if self.store.kv_get(f"{self._task_base(req_id, task)}/status")[0]
        }
        definition = dag[current_task]
        declared = definition.get("routing_policy", {}).get("allowed_recovery_targets", [])
        if declared:
            visited &= set(declared) | {current_task}
        visited = {
            task for task in visited
            if not (
                dag[task].get("side_effecting")
                and not dag[task].get("compensation_task")
            )
        }
        targets = sorted(visited, key=lambda item: (item != current_task, item))
        pending = self._read_json(
            f"{self._task_base(req_id, current_task)}/routing/pending"
        )
        if pending and pending.get("verdict") == "PASS":
            targets.append("__complete__")
        return targets

    def submit_route(self, req_id: str, current_task: str, *, target_task: str,
                     reason: str, evidence: str, still_valid: list[str],
                     invalidated: list[str], actor: str,
                     failure_fingerprint: str = "") -> dict[str, Any]:
        self._ensure_business_allowed(req_id, current_task, allow_pending_route=True)
        base = self._task_base(req_id, current_task)
        pending = self._read_json(f"{base}/routing/pending")
        if not pending:
            raise AdaptiveControlError("E_ROUTE_NOT_READY", "a check is required before routing")
        allowed = self.allowed_recovery_targets(req_id, current_task)
        if target_task not in allowed:
            raise AdaptiveControlError("E_INVALID_TARGET", "recovery target is not allowed", allowed)
        dag = self._load_dag(req_id)
        known = set(dag)
        valid_set, invalid_set = set(still_valid), set(invalidated)
        if (not _required(reason, "reason") or not _required(evidence, "evidence")
                or not valid_set <= known or not invalid_set <= known
                or valid_set & invalid_set):
            raise AdaptiveControlError("E_INVALID_ROUTE", "invalid route validity declaration")
        if target_task == "__complete__":
            if pending.get("verdict") != "PASS":
                raise AdaptiveControlError("E_COMPLETE_NOT_AUTHORIZED", "completion requires PASS")
            if invalid_set or current_task not in valid_set:
                raise AdaptiveControlError(
                    "E_INVALID_ROUTE",
                    "completion requires no invalidated tasks and current task in still_valid",
                )
            missing = self._missing_completion_requirements(req_id, current_task)
            if missing:
                raise AdaptiveControlError(
                    "E_COMPLETION_CONTRACT", "completion contract is not satisfied", missing,
                )
            status, status_index = self.store.kv_get(f"{base}/status")
            if status != "IN_PROGRESS":
                raise AdaptiveControlError(
                    "E_INVALID_TASK_STATE", f"task status is {status}, expected IN_PROGRESS"
                )
            if not self.store.kv_put(f"{base}/status", "DONE", cas=status_index):
                raise AdaptiveControlError("E_CONCURRENT_UPDATE", "task status changed concurrently")
            self.store.kv_put(f"{base}/validity", "VALID")
            self.store.kv_delete(f"{base}/routing/pending")
            decision = {
                "route_id": f"route-{uuid.uuid4().hex}",
                "source_task": current_task, "target_task": "__complete__",
                "reason": reason, "evidence": evidence,
                "still_valid": sorted(valid_set), "invalidated": [],
                "actor": _required(actor, "actor"), "created_at": _now_iso(),
                "run_id": self._current_run(req_id),
            }
            self.store.kv_put(
                f"workflows/{req_id}/routes/{decision['route_id']}", _json(decision)
            )
            self.store.kv_put(f"workflows/{req_id}/routes/current", decision["route_id"])
            self.record_event(
                req_id, current_task, "ROUTE_APPLIED", actor, decision,
                causation_id=pending["check_id"], correlation_id=decision["route_id"],
            )
            run_id = self._current_run(req_id)
            if run_id:
                self.runs.record_transition(
                    req_id, run_id, current_task, "IN_PROGRESS", "DONE", actor,
                    reason="adaptive check passed and completion contract satisfied",
                    metadata={"route_id": decision["route_id"]},
                )
                self.runs.check_run_completion(req_id, run_id)
            return decision

        computed = affected_downstream_closure(dag, [target_task])
        if invalid_set != computed:
            raise AdaptiveControlError(
                "E_INVALID_CLOSURE", "invalidated must equal the DAG downstream closure",
                {"expected": sorted(computed), "actual": sorted(invalid_set)},
            )
        if not valid_set.isdisjoint(computed):
            raise AdaptiveControlError("E_INVALID_ROUTE", "still-valid tasks intersect invalidation closure")

        lock_key = f"workflows/{req_id}/adaptive_route_lock"
        lock_id = uuid.uuid4().hex
        if not self.store.kv_put(lock_key, lock_id, cas=0):
            raise AdaptiveControlError("E_CONCURRENT_ROUTE", "another route is being applied")
        try:
            fingerprint = failure_fingerprint or hashlib.sha256(
                (current_task + "\0" + target_task + "\0" + evidence).encode("utf-8")
            ).hexdigest()
            budget_state = self._consume_route_budget(
                req_id, current_task, target_task, fingerprint
            )
            route_id = f"route-{uuid.uuid4().hex}"
            previous_run, _ = self.store.kv_get(f"workflows/{req_id}/current_run")
            impacted = invalidate_impacted_tasks(
                self.store, req_id, dag, [target_task], change_id=route_id,
            )
            for task in valid_set:
                self.store.kv_put(f"{self._task_base(req_id, task)}/validity", "VALID")
            if previous_run:
                new_run = self.runs.roll_forward_run(
                    req_id, actor=actor, change_id=route_id,
                    affected_tasks=sorted(impacted),
                )
            else:
                new_run = self.runs.get_or_create_run(req_id, actor)
            decision = {
                "route_id": route_id, "source_task": current_task,
                "target_task": target_task, "reason": reason, "evidence": evidence,
                "still_valid": sorted(valid_set), "invalidated": sorted(invalid_set),
                "failure_fingerprint": fingerprint, "actor": _required(actor, "actor"),
                "created_at": _now_iso(), "previous_run_id": previous_run or "",
                "new_run_id": new_run, "budget": budget_state,
            }
            self.store.kv_put(f"workflows/{req_id}/routes/{route_id}", _json(decision))
            self.store.kv_put(f"workflows/{req_id}/routes/current", route_id)
            self.store.kv_delete(f"{base}/routing/pending")
            self.record_event(req_id, current_task, "ROUTE_APPLIED", actor, decision,
                              causation_id=pending["check_id"], correlation_id=route_id,
                              run_id=new_run)
            for task in sorted(impacted):
                self.record_event(
                    req_id, task, "TASK_INVALIDATED", actor,
                    {"route_id": route_id, "target_task": target_task},
                    causation_id=route_id, correlation_id=route_id, run_id=new_run,
                )
            return decision
        finally:
            current_lock, _ = self.store.kv_get(lock_key)
            if current_lock == lock_id:
                self.store.kv_delete(lock_key)

    # -- Human feedback -------------------------------------------------

    def apply_control(self, req_id: str, *, action: str, actor: str,
                      reason: str, task: str = "") -> dict[str, Any]:
        """Apply a structured hard control with task scope taking precedence."""
        action = action.upper()
        if action not in {"PAUSE", "RESUME", "ABORT"}:
            raise AdaptiveControlError("E_INVALID_CONTROL", "control must be PAUSE, RESUME, or ABORT")
        key = (f"{self._task_base(req_id, task)}/control" if task
               else f"workflows/{req_id}/control")
        if action == "RESUME":
            self.store.kv_delete(key)
        else:
            self.store.kv_put(key, action)
        event_task = task or "__workflow__"
        event = self.record_event(
            req_id, event_task, "CONTROL_APPLIED", _required(actor, "actor"),
            {"action": action, "scope": f"task:{task}" if task else "workflow",
             "reason": _required(reason, "reason")},
        )
        return {"action": action, "task": task, "event": event}

    def deliver_feedback(self, req_id: str, task: str, *, message: str,
                         actor: str, kind: str = "message",
                         source: dict[str, Any] | None = None) -> dict[str, Any]:
        kind = kind.lower()
        if kind not in {"message", "answer"}:
            raise AdaptiveControlError("E_INVALID_FEEDBACK", "kind must be message or answer")
        item = {
            "feedback_id": f"HF-{uuid.uuid4().hex[:12].upper()}",
            "kind": kind, "message": _required(message, "message"),
            "actor": _required(actor, "actor"), "status": "DELIVERED",
            "source": source or {}, "created_at": _now_iso(),
        }
        self._write_feedback(req_id, task, item)
        self.record_event(req_id, task, "FEEDBACK_DELIVERED", actor,
                          {"feedback_id": item["feedback_id"], "kind": kind})
        return item

    def list_feedback(self, req_id: str, task: str,
                      unresolved_only: bool = False) -> list[dict[str, Any]]:
        prefix = f"{self._task_base(req_id, task)}/human/feedback/"
        items, _ = self.store.kv_get(prefix, recurse=True)
        rows = []
        for raw in items or []:
            try:
                row = json.loads(raw.get("_decoded", "{}"))
            except json.JSONDecodeError:
                continue
            if unresolved_only and row.get("status") == "APPLIED":
                continue
            rows.append(row)
        rows.sort(key=lambda row: str(row.get("created_at", "")))
        return rows

    def respond_feedback(self, req_id: str, task: str, *, feedback_id: str,
                         decision: str, understanding: str, reason: str,
                         impact: dict[str, Any], actor: str,
                         question: dict[str, Any] | None = None) -> dict[str, Any]:
        item = self._read_json(
            f"{self._task_base(req_id, task)}/human/feedback/{feedback_id}"
        )
        if not item or item.get("status") != "OBSERVED":
            raise AdaptiveControlError("E_FEEDBACK_NOT_OBSERVED", "feedback must be observed")
        decision = decision.upper()
        if decision not in FEEDBACK_DECISIONS:
            raise AdaptiveControlError("E_INVALID_FEEDBACK_DECISION", "invalid feedback decision")
        if not isinstance(impact, dict):
            raise AdaptiveControlError("E_INVALID_FEEDBACK", "impact must be an object")
        item.update({
            "decision": decision, "understanding": _required(understanding, "understanding"),
            "response_reason": _required(reason, "reason"), "impact": impact,
            "acknowledged_by": _required(actor, "actor"), "acknowledged_at": _now_iso(),
        })
        base = self._task_base(req_id, task)
        if decision == "ASK":
            if (not isinstance(question, dict) or not isinstance(question.get("text"), str)
                    or not question["text"].strip()
                    or not isinstance(question.get("options", []), list)):
                raise AdaptiveControlError("E_QUESTION_REQUIRED", "ASK requires text and options")
            item["status"] = "ACKNOWLEDGED"
            previous, _ = self.store.kv_get(f"{base}/status")
            opened = {
                "question_id": f"Q-{uuid.uuid4().hex[:12].upper()}",
                "feedback_id": feedback_id, "text": question["text"].strip(),
                "options": question.get("options", []), "status": "OPEN",
                "asked_by": actor, "asked_at": _now_iso(),
                "previous_task_status": previous or "IN_PROGRESS",
            }
            self.store.kv_put(f"{base}/human/question/current", _json(opened))
            self.store.kv_put(f"{base}/status", "WAITING_FOR_HUMAN")
            self.record_event(req_id, task, "QUESTION_OPENED", actor, opened)
        elif decision == "PAUSE":
            item["status"] = "APPLIED"
            item["applied_at"] = _now_iso()
            self.store.kv_put(f"{base}/control", "PAUSE")
        else:
            item["status"] = "APPLIED"
            item["applied_at"] = _now_iso()
        self._write_feedback(req_id, task, item)
        self.record_event(req_id, task, "FEEDBACK_ACKNOWLEDGED", actor,
                          {"feedback_id": feedback_id, "decision": decision})
        if item["status"] == "APPLIED":
            self.record_event(req_id, task, "FEEDBACK_APPLIED", actor,
                              {"feedback_id": feedback_id, "impact": impact})
        return item

    def answer_question(self, req_id: str, task: str, *, answer: str,
                        actor: str) -> dict[str, Any]:
        base = self._task_base(req_id, task)
        question = self._read_json(f"{base}/human/question/current")
        if not question or question.get("status") != "OPEN":
            raise AdaptiveControlError("E_NO_ACTIVE_QUESTION", "no active human question")
        feedback = self.deliver_feedback(
            req_id, task, message=answer, actor=actor, kind="answer",
            source={"question_id": question["question_id"]},
        )
        question.update({"status": "ANSWERED", "answer_feedback_id": feedback["feedback_id"],
                         "answered_at": _now_iso(), "answered_by": actor})
        self.store.kv_put(
            f"{base}/human/question/history/{question['question_id']}", _json(question)
        )
        self.store.kv_delete(f"{base}/human/question/current")
        self.store.kv_put(f"{base}/status", question["previous_task_status"])
        original = self._read_json(f"{base}/human/feedback/{question['feedback_id']}")
        if original:
            original.update({"status": "APPLIED", "applied_at": _now_iso(),
                             "resolved_by": feedback["feedback_id"]})
            self._write_feedback(req_id, task, original)
        self.record_event(req_id, task, "QUESTION_ANSWERED", actor, question)
        return {"question": question, "feedback": feedback}

    # -- Audit ----------------------------------------------------------

    def record_event(self, req_id: str, task: str, event_type: str, actor: str,
                     payload: dict[str, Any], *, causation_id: str = "",
                     correlation_id: str = "", run_id: str = "") -> dict[str, Any]:
        event = {
            "event_id": f"evt-{uuid.uuid4().hex}", "type": event_type,
            "req_id": req_id, "run_id": run_id or self._current_run(req_id),
            "task": task, "actor": actor, "timestamp": _now_iso(),
            "causation_id": causation_id, "correlation_id": correlation_id,
            "payload": payload,
        }
        self.store.kv_put(
            f"workflows/{req_id}/events/{_event_seq()}-{event['event_id']}", _json(event)
        )
        return event

    # -- Internal helpers ----------------------------------------------

    def _ensure_business_allowed(self, req_id: str, task: str,
                                 allow_pending_route: bool = False) -> None:
        boundary = self.boundary(req_id, task)
        if not boundary["blocked"]:
            return
        if allow_pending_route and boundary["kind"] == "ROUTE":
            return
        raise AdaptiveControlError("E_BOUNDARY_BLOCKED", "task is blocked at a control boundary", boundary)

    def _require_action(self, base: str, action_id: str,
                        state_version: int) -> tuple[dict[str, Any], int]:
        raw, index = self.store.kv_get(f"{base}/actions/current")
        try:
            action = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            action = None
        if not action or action.get("action_id") != action_id:
            raise AdaptiveControlError("E_STALE_ACTION", "action is not current")
        if action.get("state_version") != state_version:
            raise AdaptiveControlError("E_STALE_ACTION", "state version does not match")
        if action.get("status") != "ISSUED":
            raise AdaptiveControlError("E_ACTION_CONSUMED", "action was already consumed")
        return action, index

    @staticmethod
    def _validate_command(command: dict[str, Any]) -> None:
        if not isinstance(command, dict) or set(command) - {
                "argv", "cwd", "exit_code", "output_digest", "summary"}:
            raise AdaptiveControlError("E_INVALID_COMMAND", "invalid command evidence fields")
        argv = command.get("argv")
        if not isinstance(argv, list) or not argv or not all(
                isinstance(item, str) and item for item in argv):
            raise AdaptiveControlError("E_INVALID_COMMAND", "command argv must be a non-empty string list")
        if isinstance(command.get("exit_code"), bool) or not isinstance(command.get("exit_code"), int):
            raise AdaptiveControlError("E_INVALID_COMMAND", "command exit_code must be an integer")
        if not isinstance(command.get("cwd", ""), str):
            raise AdaptiveControlError("E_INVALID_COMMAND", "command cwd must be a string")

    def _consume_route_budget(self, req_id: str, source: str, target: str,
                              fingerprint: str) -> dict[str, Any]:
        raw = self._read_json(f"workflows/{req_id}/routing/budget") or {}
        budget = RoutingBudget.from_dict(raw.get("policy"))
        state = raw.get("state", {"total": 0, "edges": {}, "fingerprints": {}})
        edge = f"{source}->{target}"
        total = int(state.get("total", 0)) + 1
        edges = dict(state.get("edges", {})); edges[edge] = int(edges.get(edge, 0)) + 1
        fingerprints = dict(state.get("fingerprints", {}))
        fingerprints[fingerprint] = int(fingerprints.get(fingerprint, 0)) + 1
        if (total > budget.max_total_routes or edges[edge] > budget.max_same_edge_routes
                or fingerprints[fingerprint] > budget.max_same_failure_fingerprint):
            base = self._task_base(req_id, source)
            self.store.kv_put(f"{base}/status", "WAITING_FOR_HUMAN")
            raise AdaptiveControlError(
                "E_ROUTING_BUDGET_EXHAUSTED", "automatic routing budget exhausted",
                {"total": total, "edge": edges[edge],
                 "fingerprint": fingerprints[fingerprint]},
            )
        state = {"total": total, "edges": edges, "fingerprints": fingerprints}
        self.store.kv_put(f"workflows/{req_id}/routing/budget", _json({
            "policy": asdict(budget), "state": state,
        }))
        return state

    def _missing_completion_requirements(self, req_id: str, task: str) -> list[str]:
        base = self._task_base(req_id, task)
        missing: list[str] = []
        breaker = self._read_json(f"{base}/budget/circuit_breaker")
        if breaker and breaker.get("status") == "OPEN":
            missing.append("circuit_breaker:OPEN")
        contract = self._read_json(f"{base}/completion_contract")
        if not contract:
            return missing
        for artifact in contract.get("required_artifacts", []):
            version, _ = self.store.kv_get(f"{base}/artifacts/{artifact}/current_version")
            if not version:
                missing.append(f"artifact:{artifact}")
        for gate in contract.get("required_gates", []):
            verdict, _ = self.store.kv_get(f"{base}/evidence/{gate}/verdict")
            if verdict != "PASS":
                missing.append(f"gate:{gate}")
        return missing

    def _increment_version(self, base: str) -> int:
        key = f"{base}/adaptive_state_version"
        raw, index = self.store.kv_get(key)
        current = int(raw or 0)
        if not self.store.kv_put(key, str(current + 1), cas=index if raw is not None else 0):
            raise AdaptiveControlError("E_CONCURRENT_UPDATE", "adaptive state changed concurrently")
        return current + 1

    def _load_dag(self, req_id: str) -> dict[str, dict[str, Any]]:
        raw, _ = self.store.kv_get(f"workflows/{req_id}/dependencies")
        try:
            dag = json.loads(raw) if raw else None
        except json.JSONDecodeError as exc:
            raise AdaptiveControlError("E_INVALID_DAG", "workflow DAG is invalid") from exc
        if not isinstance(dag, dict):
            raise AdaptiveControlError("E_INVALID_DAG", "workflow DAG is missing")
        return dag

    @staticmethod
    def _ancestors(dag: dict[str, dict[str, Any]], task: str) -> set[str]:
        result, queue = {task}, [task]
        while queue:
            current = queue.pop(0)
            for raw in dag[current].get("depends_on", []):
                upstream = raw.get("task", "") if isinstance(raw, dict) else raw
                if upstream in dag and upstream not in result:
                    result.add(upstream); queue.append(upstream)
        return result

    def _write_feedback(self, req_id: str, task: str, item: dict[str, Any]) -> None:
        if item.get("status") not in FEEDBACK_STATES:
            raise AdaptiveControlError("E_INVALID_FEEDBACK", "invalid feedback status")
        self.store.kv_put(
            f"{self._task_base(req_id, task)}/human/feedback/{item['feedback_id']}",
            _json(item),
        )

    def _read_json(self, key: str) -> dict[str, Any] | None:
        raw, _ = self.store.kv_get(key)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _task_base(req_id: str, task: str) -> str:
        return f"workflows/{req_id}/tasks/{task}"

    def _current_run(self, req_id: str) -> str:
        value, _ = self.store.kv_get(f"workflows/{req_id}/current_run")
        return str(value or "")


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AdaptiveControlError("E_REQUIRED", f"{name} is required")
    return value.strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _event_seq() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S%f")
