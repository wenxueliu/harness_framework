#!/usr/bin/env python3
"""
worker.py — Agent Worker 持久化主循环

每个微服务代码仓常驻运行一个实例。循环: 注册 → 心跳 → 抢任务 → 执行 → 完成 → 继续。

约束:
  - 同一需求下同时只做一个任务（不同需求的任务可穿插）
  - 任务在独立 worktree 中执行
  - 支持 ABORT 全链路检测

用法:
  worker.py --service user-service --capabilities dev --repo-path /path/to/repo

环境变量:
  AGENT_ID        全局唯一 Agent ID（必填）
  CONSUL_ADDR     Consul 地址（默认 127.0.0.1:8500）
  SERVICE_NAME    绑定的服务名
  REPO_PATH       代码仓库路径
"""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)
from _consul import (  # noqa: E402
    env, kv_get, kv_put, kv_delete, emit_json, die, now_iso,
    consul_health_check, service_register_safe, service_deregister_safe,
    health_check_pass_safe,
    ensure_run, record_transition, get_current_run,
    record_session_start, record_session_end, lease_deadline_iso,
    renew_attempt_lease, check_completion_contract,
    load_declared_context,
    load_latest_checkpoint,
    build_failure_envelope,
)
from harness_framework.contracts import ReviewPolicy, ReviewResult  # noqa: E402
from harness_framework.recovery import rewind_to_task  # noqa: E402
from harness_framework.run_manager import RunManager  # noqa: E402
from harness_framework.model_execution import (  # noqa: E402
    ResolvedExecution, load_execution_profiles, resolve_execution,
)


class _WorkerKVAdapter:
    kv_get = staticmethod(kv_get)
    kv_put = staticmethod(kv_put)
    kv_delete = staticmethod(kv_delete)

# ── 状态文件 ───────────────────────────────────────────────────────────────

def state_file() -> Path:
    """worker 状态文件路径（用于跨 session 恢复）。"""
    skill_dir = Path(os.path.dirname(os.path.abspath(__file__))).parent
    return skill_dir / ".worker_state.json"


def load_state() -> dict:
    sf = state_file()
    if sf.exists():
        try:
            return json.loads(sf.read_text())
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_state(state: dict) -> None:
    state_file().write_text(json.dumps(state, ensure_ascii=False, indent=2))


def clear_state() -> None:
    sf = state_file()
    if sf.exists():
        sf.unlink(missing_ok=True)


# ── 心跳线程 ────────────────────────────────────────────────────────────────

class Heartbeat:
    """后台心跳线程，每 10 秒向 Consul 上报 TTL。"""

    def __init__(self, agent_id: str, interval: int = 10):
        self.agent_id = agent_id
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lease_lock = threading.Lock()
        self._lease: Optional[tuple[str, str, str, int, int]] = None

    def set_lease(self, req_id: str, task_name: str, attempt_id: str,
                  lease_epoch: int, duration_seconds: int) -> None:
        with self._lease_lock:
            self._lease = (req_id, task_name, attempt_id, lease_epoch,
                           duration_seconds)

    def clear_lease(self) -> None:
        with self._lease_lock:
            self._lease = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="heartbeat")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        check_id = f"service:{self.agent_id}"
        while not self._stop.is_set():
            ok, msg = health_check_pass_safe(check_id)
            if not ok:
                print(f"[heartbeat] 心跳失败: {msg}", file=sys.stderr)
            with self._lease_lock:
                lease = self._lease
            if lease:
                req_id, task_name, attempt_id, lease_epoch, duration = lease
                renewed, reason, _ = renew_attempt_lease(
                    req_id, task_name, attempt_id, str(lease_epoch), duration,
                    self.agent_id,
                )
                if not renewed:
                    print(f"[lease] 续租失败: {reason}", file=sys.stderr)
            self._stop.wait(self.interval)


# ── 任务发现与抢占 ──────────────────────────────────────────────────────────

def find_pending_tasks() -> list[dict]:
    """查找所有 PENDING 状态的任务（跨所有需求）。"""
    items, _ = kv_get("workflows", recurse=True)
    if not items:
        return []

    pending = []
    for it in items:
        key = it.get("Key", "")
        if not key.endswith("/status"):
            continue
        if it.get("_decoded", "") != "PENDING":
            continue

        # workflows/<req_id>/tasks/<task_name>/status
        parts = key.split("/")
        if len(parts) < 5 or parts[0] != "workflows" or parts[2] != "tasks":
            continue

        req_id = parts[1]
        task_name = "/".join(parts[3:-1])  # 任务名可能含斜杠
        pending.append({"req_id": req_id, "task_name": task_name})

    return pending


def load_task_meta(req_id: str, task_name: str) -> dict:
    """加载单个任务的完整元数据。"""
    base = f"workflows/{req_id}/tasks/{task_name}"
    items, _ = kv_get(base, recurse=True)
    meta = {}
    if items:
        for it in items:
            suffix = it["Key"].split(f"{base}/", 1)[-1] if "/" in it["Key"] else ""
            if suffix:
                meta[suffix] = it.get("_decoded", "")
    return meta


def load_context(req_id: str, task_name: str) -> dict:
    """加载任务显式声明的上下文。"""
    return load_declared_context(req_id, task_name)


def claim_task(req_id: str, task_name: str, agent_id: str) -> tuple[bool, dict]:
    """
    CAS 原子抢占任务。返回 (success, result_dict)。
    """
    base = f"workflows/{req_id}/tasks/{task_name}"

    # 1. 读当前状态 + ModifyIndex
    status, modify_index = kv_get(f"{base}/status")
    if status is None:
        return False, {"error": f"任务 {req_id}/{task_name} 不存在"}
    if status != "PENDING":
        return False, {"error": f"任务状态为 {status}，非 PENDING"}

    # 2. CAS 抢占
    ok = kv_put(f"{base}/status", "IN_PROGRESS", cas=modify_index)
    if not ok:
        return False, {"error": "CAS 失败，其他 Agent 抢先"}

    # 3. 写入元数据
    ts = now_iso()
    lease_duration = int(env("LEASE_DURATION_SECONDS", "120"))
    hard_timeout = int(env("HARD_TASK_TIMEOUT_SECONDS", "7200"))
    previous_epoch, _ = kv_get(f"{base}/lease_epoch")
    lease_epoch = int(previous_epoch or "0") + 1
    attempt_id = f"attempt-{uuid.uuid4().hex}"
    kv_put(f"{base}/attempt_id", attempt_id)
    kv_put(f"{base}/lease_epoch", str(lease_epoch))
    kv_put(f"{base}/assigned_agent", agent_id)
    kv_put(f"{base}/started_at", ts)
    kv_put(f"{base}/lease_renewed_at", ts)
    kv_put(f"{base}/lease_expires_at", lease_deadline_iso(lease_duration))
    hard_deadline_at = lease_deadline_iso(hard_timeout)
    kv_put(f"{base}/hard_deadline_at", hard_deadline_at)
    kv_put(f"{base}/worker_pid", str(os.getpid()))

    # 4. 记录状态转换
    run_id = ensure_run(req_id)
    record_transition(
        req_id, run_id, task_name,
        previous_state="PENDING",
        new_state="IN_PROGRESS",
        actor=agent_id,
        reason="claimed by worker",
    )

    # 5. 读取完整上下文
    task_meta = load_task_meta(req_id, task_name)
    context = load_context(req_id, task_name)

    return True, {
        "req_id": req_id,
        "task_name": task_name,
        "attempt_id": attempt_id,
        "lease_epoch": lease_epoch,
        "lease_duration_seconds": lease_duration,
        "hard_deadline_at": hard_deadline_at,
        "task_meta": task_meta,
        "context": context,
        "resume_checkpoint": load_latest_checkpoint(req_id, task_name),
    }


def complete_task(req_id: str, task_name: str, agent_id: str,
                  result: dict = None, attempt_id: str = "",
                  lease_epoch: int = 0,
                  final_status: str = "DONE") -> bool:
    """标记任务完成。"""
    if final_status not in {"DONE", "AWAITING_REVIEW"}:
        raise ValueError("invalid completion status")
    base = f"workflows/{req_id}/tasks/{task_name}"
    ts = now_iso()
    current_attempt, _ = kv_get(f"{base}/attempt_id")
    current_epoch, _ = kv_get(f"{base}/lease_epoch")
    if current_attempt != attempt_id or str(current_epoch) != str(lease_epoch):
        return False
    prev_status, status_idx = kv_get(f"{base}/status")
    if prev_status != "IN_PROGRESS":
        return False
    ready, _ = check_completion_contract(req_id, task_name)
    if not ready:
        return False
    run_id = ensure_run(req_id)
    record_transition(
        req_id, run_id, task_name,
        previous_state=prev_status or "IN_PROGRESS",
        new_state=final_status,
        actor=agent_id,
        reason=("awaiting human approval" if final_status == "AWAITING_REVIEW"
                else "task completed"),
    )
    if not kv_put(f"{base}/status", final_status, cas=status_idx):
        return False
    if final_status == "DONE":
        kv_put(f"{base}/validity", "VALID")
    kv_delete(f"{base}/recovery_feedback/current")
    kv_put(f"{base}/completed_by", agent_id)
    kv_put(f"{base}/completed_at", ts)
    if result:
        kv_put(f"{base}/result", json.dumps(result, ensure_ascii=False))
    return True


def fail_task(req_id: str, task_name: str, agent_id: str,
              error: str, retry_hint: str = "retry", attempt_id: str = "",
              lease_epoch: int = 0) -> bool:
    """标记任务失败。"""
    base = f"workflows/{req_id}/tasks/{task_name}"
    ts = now_iso()
    # 记录状态转换
    current_attempt, _ = kv_get(f"{base}/attempt_id")
    current_epoch, _ = kv_get(f"{base}/lease_epoch")
    if current_attempt != attempt_id or str(current_epoch) != str(lease_epoch):
        return False
    prev_status, status_idx = kv_get(f"{base}/status")
    if prev_status != "IN_PROGRESS":
        return False
    run_id = ensure_run(req_id)
    record_transition(
        req_id, run_id, task_name,
        previous_state=prev_status or "IN_PROGRESS",
        new_state="FAILED",
        actor=agent_id,
        reason=error,
    )
    if not kv_put(f"{base}/status", "FAILED", cas=status_idx):
        return False
    kv_put(f"{base}/failed_by", agent_id)
    kv_put(f"{base}/failed_at", ts)
    kv_put(f"{base}/error_message", error)
    kv_put(f"{base}/retry_hint", retry_hint)
    envelope = build_failure_envelope(
        task_name=task_name, attempt_id=attempt_id, lease_epoch=lease_epoch,
        message=error, retryable=retry_hint == "retry",
    )
    kv_put(f"{base}/failure/current", json.dumps(envelope, ensure_ascii=False))
    kv_put(
        f"{base}/failure/history/{envelope['failure_id']}",
        json.dumps(envelope, ensure_ascii=False),
    )
    return True


def log_step(req_id: str, task_name: str, agent_id: str,
             step_type: str, message: str,
             level: str = "info", data: dict = None) -> None:
    """记录执行步骤到会话流（JSON blob 格式，与 log_step.py 一致）。"""
    session_id = f"{agent_id}-{int(time.time())}"
    ts = now_iso()
    seq = str(int(time.time() * 1000000))
    run_id = get_current_run(req_id) or ""
    payload = {
        "ts": ts,
        "agent_id": agent_id,
        "level": level,
        "message": message,
        "step_type": step_type,
        "run_id": run_id,
        "data": data or {},
    }
    base = f"workflows/{req_id}/sessions/{task_name}/{session_id}/events/{seq}"
    kv_put(base, json.dumps(payload, ensure_ascii=False))


def check_control(req_id: str) -> Optional[str]:
    """检查控制信号。返回 'ABORT', 'PAUSE', 或 None。"""
    ctl, _ = kv_get(f"workflows/{req_id}/control")
    if ctl in ("ABORT", "PAUSE"):
        return ctl
    return None


# ── 任务过滤 ────────────────────────────────────────────────────────────────

def rank_tasks(tasks: list[dict], agent_id: str,
               service_name: str, capabilities: list[str],
               skip_req_id: Optional[str] = None) -> list[dict]:
    """
    过滤 + 排序任务。优先:
    1. 需求优先级（高 → 低）
    2. 匹配 service_name
    3. 匹配 capability
    4. 排除 skip_req_id（当前正在做的需求）

    注意: type 过滤是软过滤 —— 能做的都做，只是优先级不同。
    """
    # 批量加载 deps 以获取依赖计数
    deps_cache = {}

    def get_dep_count(req_id: str, task_name: str) -> int:
        cache_key = (req_id, task_name)
        if cache_key not in deps_cache:
            deps_str, _ = kv_get(f"workflows/{req_id}/dependencies")
            if deps_str:
                try:
                    deps = json.loads(deps_str)
                    info = deps.get(task_name, {})
                    deps_cache[cache_key] = len(info.get("depends_on", []))
                except json.JSONDecodeError:
                    deps_cache[cache_key] = 0
            else:
                deps_cache[cache_key] = 0
        return deps_cache[cache_key]

    def req_priority(req_id: str) -> int:
        pri, _ = kv_get(f"workflows/{req_id}/priority")
        return int(pri) if pri else 0

    ranked = []
    for task in tasks:
        req_id = task["req_id"]
        task_name = task["task_name"]

        # 排除当前需求
        if skip_req_id and req_id == skip_req_id:
            continue

        meta = load_task_meta(req_id, task_name)
        task_service = meta.get("service_name", "")
        task_type = meta.get("type", "backend")
        task_cap = meta.get("capability", "")

        score = 0

        # 需求优先级（权重最大）
        score += req_priority(req_id) * 1000

        # 匹配 service_name（最优先）
        if service_name and task_service == service_name:
            score += 500
        elif service_name and task_service == "_test":
            score += 200  # 测试任务也可接受
        elif service_name and task_service != service_name:
            score -= 100  # 不太匹配的服务

        # 匹配 capability
        if task_cap and task_cap in capabilities:
            score += 300
        elif task_type in capabilities:
            score += 200
        # type fallback
        type_to_cap = {"design": "design", "review": "review",
                        "backend": "dev", "test": "test", "deploy": "deploy"}
        if type_to_cap.get(task_type) in capabilities:
            score += 150

        # 依赖少的优先（避免阻塞其他任务）
        dep_count = get_dep_count(req_id, task_name)
        score += max(0, 50 - dep_count * 5)

        ranked.append((score, task, meta))

    ranked.sort(key=lambda x: -x[0])
    return [(t, m) for _, t, m in ranked]


# ── 任务执行 ────────────────────────────────────────────────────────────────

def _json_meta(meta: dict, name: str, default):
    value = meta.get(name)
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _run_json_command(command: list[str], payload: dict, timeout: int) -> dict:
    """Run one executor/reviewer command using the JSON stdin/stdout contract."""
    proc = subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command exited {proc.returncode}: {proc.stderr[:1000]}"
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("command stdout must be a JSON object") from exc
    if not isinstance(result, dict):
        raise ValueError("command stdout must be a JSON object")
    return result


def _native_session_lock_key(provider: str, session_id: str) -> str:
    digest = hashlib.sha256(f"{provider}:{session_id}".encode()).hexdigest()
    return f"session-locks/native/{digest}"


def _acquire_native_session_lock(
    resolved: ResolvedExecution, owner: str, timeout: int,
) -> str:
    if not resolved.native_session_id:
        return ""
    key = _native_session_lock_key(resolved.provider, resolved.native_session_id)
    current, index = kv_get(key)
    now = time.time()
    if current:
        try:
            record = json.loads(current)
        except json.JSONDecodeError:
            record = {}
        if record.get("owner") != owner and float(record.get("expires_at", 0)) > now:
            raise RuntimeError(
                f"native session is already in use: {resolved.native_session_id}"
            )
    value = json.dumps({
        "owner": owner,
        "provider": resolved.provider,
        "session_id": resolved.native_session_id,
        "expires_at": now + timeout + 60,
    })
    if not kv_put(key, value, cas=index):
        raise RuntimeError(
            f"failed to acquire native session lock: {resolved.native_session_id}"
        )
    return key


def _release_native_session_lock(key: str, owner: str) -> None:
    if not key:
        return
    current, _ = kv_get(key)
    try:
        record = json.loads(current) if current else {}
    except json.JSONDecodeError:
        record = {}
    if record.get("owner") == owner:
        kv_delete(key)


def _resolve_task_execution(
    req_id: str, task_name: str, task_meta: dict, config: dict,
) -> ResolvedExecution | None:
    raw = _json_meta(task_meta, "execution", None)
    if not raw:
        depends_on = task_meta.get("depends_on", "")
        if isinstance(depends_on, str):
            upstream_tasks = [
                item.strip() for item in depends_on.split(",") if item.strip()
            ]
        elif isinstance(depends_on, list):
            upstream_tasks = [
                item.get("task", "") if isinstance(item, dict) else str(item)
                for item in depends_on
            ]
            upstream_tasks = [item for item in upstream_tasks if item]
        else:
            upstream_tasks = []

        candidates = []
        for source_task in upstream_tasks:
            native_session_id, _ = kv_get(
                f"workflows/{req_id}/tasks/{source_task}/native_session_id"
            )
            if native_session_id:
                candidates.append((source_task, native_session_id))
        if len(candidates) > 1:
            names = ", ".join(source for source, _sid in candidates)
            raise ValueError(
                "task has multiple resumable upstream sessions; configure "
                f"execution.session.from_task explicitly: {names}"
            )
        if len(candidates) == 1:
            source_task, native_session_id = candidates[0]
            inherited, _ = kv_get(
                f"workflows/{req_id}/tasks/{source_task}/execution_effective"
            )
            if not inherited:
                inherited, _ = kv_get(
                    f"workflows/{req_id}/tasks/{source_task}/execution"
                )
            if not inherited:
                raise ValueError(
                    f"upstream task has a native session but no execution "
                    f"configuration: {source_task}"
                )
            try:
                raw = json.loads(inherited) if isinstance(inherited, str) else inherited
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"upstream task has invalid execution configuration: {source_task}"
                ) from exc
            raw = json.loads(json.dumps(raw))
            raw["session"] = {
                "mode": "continue", "from_task": source_task,
            }
    if not raw:
        return None

    def lookup(source_task: str) -> str | None:
        value, _ = kv_get(
            f"workflows/{req_id}/tasks/{source_task}/native_session_id"
        )
        return value

    resolved = resolve_execution(
        raw, config.get("execution_profiles", {}), lookup,
        allowed_executables=config.get("allowed_executables", set()),
    )
    kv_put(
        f"workflows/{req_id}/tasks/{task_name}/execution_effective",
        json.dumps(raw, ensure_ascii=False),
    )
    return resolved


def _record_review_round(req_id: str, task_name: str, round_no: int,
                         review_input: dict, review_result: dict,
                         attempt_id: str) -> None:
    base = (
        f"workflows/{req_id}/tasks/{task_name}/review/attempts/"
        f"{attempt_id}/rounds/{round_no}"
    )
    kv_put(f"{base}/input", json.dumps(review_input, ensure_ascii=False))
    kv_put(f"{base}/output", json.dumps(review_result, ensure_ascii=False))
    kv_put(f"{base}/verdict", review_result["verdict"])
    kv_put(f"{base}/reviewer", review_result.get("reviewer", ""))
    kv_put(
        f"workflows/{req_id}/tasks/{task_name}/review/current_round",
        str(round_no),
    )


def _record_review_pass(req_id: str, task_name: str, agent_id: str,
                        round_no: int, review_result: dict,
                        attempt_id: str, lease_epoch: int) -> None:
    reviewer = review_result.get("reviewer") or agent_id
    base = f"workflows/{req_id}/tasks/{task_name}/evidence/review"
    record = {
        "gate": "review",
        "verdict": "PASS",
        "verifier": reviewer,
        "observed_at": now_iso(),
        "details": {
            "round": round_no,
            "summary": review_result.get("summary", ""),
        },
        "artifact_refs": review_result.get("artifact_refs", []),
        "producer_attempt_id": attempt_id,
        "producer_lease_epoch": lease_epoch,
    }
    kv_put(f"{base}/verdict", "PASS")
    kv_put(f"{base}/record", json.dumps(record, ensure_ascii=False))


def execute_task(req_id: str, task_name: str, task_meta: dict,
                 context: dict, config: dict) -> dict:
    """
    执行单个任务。

    调用外部 executor 完成实际开发工作。executor 接收 JSON 输入:
      {
        "req_id": "...",
        "task_name": "...",
        "task_meta": {...},
        "context": {...},
        "config": {...}
      }

    返回 {"status": "DONE" | "FAILED", ...}
    """
    agent_id = config["agent_id"]
    try:
        resolved_execution = _resolve_task_execution(
            req_id, task_name, task_meta, config
        )
    except (ValueError, OSError) as exc:
        return {"status": "FAILED", "error": str(exc)}
    executor = (
        resolved_execution.command if resolved_execution else config.get("executor")
    )
    reviewer = config.get("reviewer")

    # 记录 session 开始
    run_id = get_current_run(req_id) or ensure_run(req_id)
    session_id = f"{agent_id}-{int(time.time())}"
    record_session_start(req_id, run_id, task_name, session_id, agent_id)
    kv_put(f"workflows/{req_id}/tasks/{task_name}/harness_session_id", session_id)
    if resolved_execution:
        execution_base = f"workflows/{req_id}/tasks/{task_name}/execution_resolved"
        kv_put(f"{execution_base}/provider", resolved_execution.provider)
        kv_put(f"{execution_base}/model", resolved_execution.model)
        kv_put(f"{execution_base}/session_mode", resolved_execution.session_mode)
        kv_put(f"{execution_base}/profile", resolved_execution.profile)
    error_count = 0

    log_step(req_id, task_name, agent_id, "EXEC_START",
             f"开始执行 {task_name} (type={task_meta.get('type')})")

    if not executor:
        # 无 executor：占位模式，标记为 DONE（等待 step 3 适配 TDD executor）
        print(f"[worker] 无 executor，任务 {task_name} 标记为 DONE（占位模式）", file=sys.stderr)
        log_step(req_id, task_name, agent_id, "EXEC_END",
                 f"占位模式完成 {task_name}")
        record_session_end(req_id, run_id, task_name, event_count=1, error_count=0,
                           status="completed", summary=f"占位模式完成 {task_name}")
        return {"status": "DONE", "mode": "placeholder", "message": "无 executor，跳过实际执行"}

    session_lock = ""
    lock_owner = f"{agent_id}:{config.get('attempt_id', session_id)}"
    try:
        if resolved_execution:
            session_lock = _acquire_native_session_lock(
                resolved_execution, lock_owner, config.get("task_timeout", 7200)
            )
        policy_raw = _json_meta(task_meta, "review_policy", None)
        policy = ReviewPolicy.from_dict(policy_raw) if policy_raw else None
        if policy and not reviewer:
            raise ValueError("task has review_policy but worker has no --reviewer")

        recovery_feedback, _ = kv_get(
            f"workflows/{req_id}/tasks/{task_name}/recovery_feedback/current"
        )
        human_feedback, _ = kv_get(
            f"workflows/{req_id}/tasks/{task_name}/review/human_feedback"
        )
        raw_feedback = recovery_feedback or human_feedback
        feedback = json.loads(raw_feedback) if raw_feedback else None
        max_rounds = policy.max_rounds if policy else 1
        last_result = {}
        native_session_id = resolved_execution.native_session_id if resolved_execution else ""

        for round_no in range(1, max_rounds + 1):
            task_input = {
                "req_id": req_id,
                "task_name": task_name,
                "round": round_no,
                "attempt_id": config.get("attempt_id", ""),
                "lease_epoch": config.get("lease_epoch", 0),
                "task_meta": task_meta,
                "context": context,
                "review_feedback": feedback,
                "config": {
                    "agent_id": agent_id,
                    "service_name": config.get("service_name", ""),
                    "repo_path": config.get("repo_path", ""),
                    "worktree_base": config.get("worktree_base", ".worktree"),
                    "execution": ({
                        "provider": resolved_execution.provider,
                        "model": resolved_execution.model,
                        "session_mode": resolved_execution.session_mode,
                        "native_session_id": resolved_execution.native_session_id,
                    } if resolved_execution else None),
                },
            }
            last_result = _run_json_command(
                executor, task_input, config.get("task_timeout", 7200)
            )
            if last_result.get("status") != "DONE":
                return last_result
            if resolved_execution:
                native_session_id = (
                    last_result.get("native_session_id")
                    or last_result.get("session_id")
                    or resolved_execution.native_session_id
                )
                if native_session_id:
                    kv_put(
                        f"workflows/{req_id}/tasks/{task_name}/native_session_id",
                        native_session_id,
                    )
            if not policy:
                break

            review_input = {
                "req_id": req_id,
                "task_name": task_name,
                "round": round_no,
                "attempt_id": config.get("attempt_id", ""),
                "task": {
                    "description": task_meta.get("description", ""),
                    "agent_contract": _json_meta(
                        task_meta, "agent_contract", {}
                    ),
                },
                "acceptance": {
                    "completion_contract": _json_meta(
                        task_meta, "completion_contract", {}
                    ),
                    "dimensions": policy.dimensions,
                    "blocking_severities": policy.blocking_severities,
                    "allowed_recovery_targets": (
                        policy.allowed_recovery_targets or [task_name]
                    ),
                    "default_recovery_target": (
                        policy.default_recovery_target or task_name
                    ),
                },
                "context": context,
                "execution_result": last_result,
            }
            parsed = ReviewResult.from_dict(
                _run_json_command(
                    reviewer, review_input, config.get("review_timeout", 1800)
                )
            )
            review_result = parsed.to_dict()
            if policy.require_independent_agent:
                if not parsed.reviewer:
                    raise ValueError("independent reviewer must return reviewer identity")
                if parsed.reviewer == agent_id:
                    raise ValueError("executor cannot review its own task")
            _record_review_round(
                req_id, task_name, round_no, review_input, review_result,
                config.get("attempt_id", ""),
            )
            log_step(
                req_id, task_name, agent_id, "REVIEW",
                f"review round {round_no}: {parsed.verdict}",
                data={"summary": parsed.summary, "reviewer": parsed.reviewer},
            )
            if parsed.verdict == "PASS":
                _record_review_pass(
                    req_id, task_name, agent_id, round_no, review_result,
                    config.get("attempt_id", ""),
                    config.get("lease_epoch", 0),
                )
                last_result["review"] = review_result
                last_result["review_rounds"] = round_no
                break
            if parsed.verdict == "ERROR":
                return {
                    "status": "FAILED",
                    "error": parsed.summary or "reviewer returned ERROR",
                }
            recovery_target = (
                parsed.recovery_target
                or policy.default_recovery_target
                or task_name
            )
            allowed_targets = policy.allowed_recovery_targets or [task_name]
            if recovery_target not in allowed_targets:
                raise ValueError(
                    f"reviewer selected disallowed recovery target: "
                    f"{recovery_target}"
                )
            if recovery_target != task_name:
                return {
                    "status": "REWIND_REQUIRED",
                    "target_task": recovery_target,
                    "review": review_result,
                }
            feedback = review_result
            if resolved_execution and native_session_id and not session_lock:
                resume_execution = json.loads(json.dumps(
                    _json_meta(task_meta, "execution", {})
                ))
                resume_execution["session"] = {
                    "mode": "resume", "session_id": native_session_id,
                }
                resolved_execution = resolve_execution(
                    resume_execution, config.get("execution_profiles", {}),
                    lambda _task: None,
                    allowed_executables=config.get("allowed_executables", set()),
                )
                executor = resolved_execution.command
                session_lock = _acquire_native_session_lock(
                    resolved_execution, lock_owner,
                    config.get("task_timeout", 7200),
                )
        else:
            return {
                "status": "FAILED",
                "error": f"review did not pass after {max_rounds} rounds",
                "review": feedback,
            }

        log_step(req_id, task_name, agent_id, "EXEC_END",
                 f"任务执行成功: {task_name}")
        record_session_end(req_id, run_id, task_name, event_count=2,
                           error_count=0, status="completed",
                           summary=f"任务 {task_name} 执行成功")
        if policy:
            last_result["human_approval_required"] = (
                policy.human_approval_after_pass
            )
        return last_result

    except subprocess.TimeoutExpired:
        log_step(req_id, task_name, agent_id, "EXEC_TIMEOUT",
                 f"任务超时: {task_name}")
        record_session_end(req_id, run_id, task_name, event_count=2, error_count=1,
                           status="error", summary="任务超时")
        return {"status": "FAILED", "error": "task_timeout"}
    except Exception as e:
        log_step(req_id, task_name, agent_id, "EXEC_ERROR",
                 f"executor 异常: {e}")
        record_session_end(req_id, run_id, task_name, event_count=2, error_count=1,
                           status="error", summary=str(e))
        return {"status": "FAILED", "error": str(e)}
    finally:
        _release_native_session_lock(session_lock, lock_owner)


# ── 主循环 ──────────────────────────────────────────────────────────────────

class Worker:
    """Agent Worker: 持久化任务执行循环。"""

    def __init__(self, config: dict):
        self.config = config
        self.agent_id = config["agent_id"]
        self.service_name = config.get("service_name", "")
        self.capabilities = config.get("capabilities", [])
        self.poll_interval = config.get("poll_interval", 5)
        self.heartbeat = Heartbeat(self.agent_id)
        self._stop = threading.Event()
        self._current_req_id: Optional[str] = None
        self._current_task_name: Optional[str] = None

    def register(self) -> bool:
        """注册 Agent 到 Consul。"""
        agent_id = self.agent_id
        capabilities = self.capabilities
        service_name = self.service_name
        repo_path = self.config.get("repo_path", "")

        tags = [f"capability={c}" for c in capabilities]
        if service_name:
            tags.append(f"service={service_name}")

        payload = {
            "ID": agent_id,
            "Name": "agent-worker",
            "Tags": tags,
            "Meta": {
                "agent_id": agent_id,
                "capabilities": ",".join(capabilities),
                "max_concurrent": "1",
                "current_load": "0",
                "service_name": service_name,
                "repo_path": repo_path,
                "registered_at": now_iso(),
            },
            "Check": {
                "CheckID": f"service:{agent_id}",
                "Name": f"TTL check for {agent_id}",
                "TTL": "30s",
                "DeregisterCriticalServiceAfter": "2m",
            },
        }

        ok, msg = service_register_safe(payload)
        if not ok:
            print(f"[worker] 注册失败: {msg}", file=sys.stderr)
            return False

        # 同步写入 KV
        kv_put(f"agents/{agent_id}/load", "0")
        kv_put(f"agents/{agent_id}/registered_at", now_iso())
        if service_name:
            kv_put(f"agents/{agent_id}/service", service_name)

        print(f"[worker] 注册成功: {agent_id} (service={service_name}, "
              f"capabilities={capabilities})")
        return True

    def deregister(self):
        """注销 Agent。"""
        service_deregister_safe(self.agent_id)
        kv_delete(f"agents/{self.agent_id}", recurse=True)
        print(f"[worker] 已注销: {self.agent_id}")

    def start(self):
        """启动 worker 主循环。"""
        # 注册
        if not self.register():
            die("注册失败", code=2)

        # 心跳
        self.heartbeat.start()

        # 信号处理
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

        print(f"[worker] 开始轮询 (间隔: {self.poll_interval}s)", file=sys.stderr)

        # 主循环
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[worker] tick error: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc(file=sys.stderr)

            # 等待下一次轮询
            self._stop.wait(self.poll_interval)

        # 清理
        self.heartbeat.stop()
        self.deregister()
        print("[worker] 已停止", file=sys.stderr)

    def stop(self):
        self._stop.set()

    def _on_signal(self, signum, frame):
        print(f"\n[worker] 收到信号 {signum}，正在停止...", file=sys.stderr)
        self.stop()

    def _tick(self):
        """单次轮询周期。"""

        # 0. 检查当前任务是否还在执行中
        if self._current_req_id and self._current_task_name:
            # Worker 不应该在上一个任务未完成时走到这里
            # （任务执行是同步的，完成后会清理状态）
            # 但如果从状态文件恢复，可能有残留状态
            self._current_req_id = None
            self._current_task_name = None
            save_state({})

        # 1. 查找所有 PENDING 任务
        pending = find_pending_tasks()
        if not pending:
            return

        # 2. 过滤 + 排序
        ranked = rank_tasks(
            pending, self.agent_id,
            self.service_name, self.capabilities,
            skip_req_id=self._current_req_id,
        )

        if not ranked:
            return

        # 3. 尝试抢占
        task, meta = ranked[0]
        req_id = task["req_id"]
        task_name = task["task_name"]

        success, result = claim_task(req_id, task_name, self.agent_id)
        if not success:
            # 抢占失败（CAS 冲突），下次重试
            return

        # 4. 执行
        self._current_req_id = req_id
        self._current_task_name = task_name
        save_state({
            "agent_id": self.agent_id,
            "req_id": req_id,
            "task_name": task_name,
            "claimed_at": now_iso(),
            "attempt_id": result.get("attempt_id", ""),
            "lease_epoch": result.get("lease_epoch", 0),
        })
        self.heartbeat.set_lease(
            req_id, task_name, result.get("attempt_id", ""),
            result.get("lease_epoch", 0),
            result.get("lease_duration_seconds", 120),
        )

        print(f"[worker] 开始执行: {req_id}/{task_name} "
              f"(type={meta.get('type')}, service={meta.get('service_name')})")

        # 检查 ABORT
        ctl = check_control(req_id)
        if ctl == "ABORT":
            fail_task(req_id, task_name, self.agent_id,
                      "任务被 ABORT", retry_hint="manual",
                      attempt_id=result.get("attempt_id", ""),
                      lease_epoch=result.get("lease_epoch", 0))
            self._clear_current()
            return

        # 执行任务
        context = load_context(req_id, task_name)
        if result.get("resume_checkpoint"):
            context["_resume_checkpoint"] = result["resume_checkpoint"]
        execution_config = dict(self.config)
        execution_config["attempt_id"] = result.get("attempt_id", "")
        execution_config["lease_epoch"] = result.get("lease_epoch", 0)
        exec_result = execute_task(
            req_id, task_name, meta, context, execution_config
        )

        # 5. 报告结果
        if exec_result.get("status") == "DONE":
            final_status = (
                "AWAITING_REVIEW"
                if exec_result.get("human_approval_required") else "DONE"
            )
            completed = complete_task(
                req_id, task_name, self.agent_id, exec_result,
                attempt_id=result.get("attempt_id", ""),
                lease_epoch=result.get("lease_epoch", 0),
                final_status=final_status,
            )
            if not completed:
                print(f"[worker] 任务 attempt 已失效，丢弃完成结果: {req_id}/{task_name}")
                self._clear_current()
                return
            print(f"[worker] 任务状态 {final_status}: {req_id}/{task_name}")
        elif exec_result.get("status") == "REWIND_REQUIRED":
            try:
                policy = ReviewPolicy.from_dict(
                    _json_meta(meta, "review_policy", {})
                )
                recovery = rewind_to_task(
                    _WorkerKVAdapter(), req_id, task_name,
                    exec_result["target_task"], exec_result.get("review", {}),
                    actor=self.agent_id,
                    allowed_targets=(policy.allowed_recovery_targets
                                     or [task_name]),
                    run_manager=RunManager(_WorkerKVAdapter()),
                )
                print(
                    f"[worker] 任务回退到 {exec_result['target_task']}: "
                    f"{','.join(recovery['impacted_tasks'])}"
                )
            except Exception as exc:
                current_status, _ = kv_get(
                    f"workflows/{req_id}/tasks/{task_name}/status"
                )
                if current_status == "IN_PROGRESS":
                    fail_task(
                        req_id, task_name, self.agent_id,
                        f"recovery failed: {exc}", retry_hint="manual",
                        attempt_id=result.get("attempt_id", ""),
                        lease_epoch=result.get("lease_epoch", 0),
                    )
                print(f"[worker] 回退失败: {req_id}/{task_name}: {exc}")
        else:
            error = exec_result.get("error", exec_result.get("stderr", "未知错误"))
            fail_task(
                req_id, task_name, self.agent_id, error,
                attempt_id=result.get("attempt_id", ""),
                lease_epoch=result.get("lease_epoch", 0),
            )
            print(f"[worker] 任务失败: {req_id}/{task_name}: {error[:200]}")

        self._clear_current()

    def _clear_current(self):
        self.heartbeat.clear_lease()
        self._current_req_id = None
        self._current_task_name = None
        clear_state()


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Agent Worker — 持久化任务执行循环"
    )
    parser.add_argument("--service", default="",
                        help="绑定的微服务名（必填，或通过 SERVICE_NAME 环境变量）")
    parser.add_argument("--capabilities", default="dev",
                        help="逗号分隔的能力标签 (dev/test/design/review/deploy)")
    parser.add_argument("--repo-path", default="",
                        help="微服务代码仓库本地路径")
    parser.add_argument("--poll-interval", type=int, default=5,
                        help="任务轮询间隔（秒），默认 5")
    parser.add_argument("--task-timeout", type=int, default=7200,
                        help="单任务最大执行时间（秒），默认 7200 (2h)")
    parser.add_argument("--executor", default="",
                        help="任务执行脚本/程序路径。接收 JSON stdin，输出 JSON stdout")
    parser.add_argument(
        "--execution-profiles", default=env("EXECUTION_PROFILES_FILE", ""),
        help="JSON execution profile file used by task-scoped execution",
    )
    parser.add_argument(
        "--allowed-executables", default=env("ALLOWED_MODEL_EXECUTABLES", ""),
        help="Comma-separated executables allowed for direct task commands",
    )
    parser.add_argument("--reviewer", default="",
                        help="独立评审脚本/程序。接收 Review Package JSON，输出 ReviewResult JSON")
    parser.add_argument("--review-timeout", type=int, default=1800,
                        help="单轮评审最长时间（秒），默认 1800")
    parser.add_argument("--worktree-base", default=".worktree",
                        help="Worktree 目录前缀，默认 .worktree")
    parser.add_argument("--once", action="store_true",
                        help="单次模式：抢一个任务执行后退出（不循环）")
    parser.add_argument("--agent-id", default="",
                        help="Agent ID（默认从环境变量 AGENT_ID 读取）")
    args = parser.parse_args()

    # 配置
    agent_id = args.agent_id or env("AGENT_ID", required=True)
    service_name = args.service or env("SERVICE_NAME", "")
    repo_path = args.repo_path or env("REPO_PATH", os.getcwd())
    capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]
    try:
        execution_profiles = load_execution_profiles(args.execution_profiles)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"cannot load execution profiles: {exc}")
    allowed_executables = {
        item.strip() for item in args.allowed_executables.split(",") if item.strip()
    }
    allowed_executables.update(
        os.path.basename(profile["command"][0])
        for profile in execution_profiles.values()
    )

    if not service_name:
        print("[worker] 警告: 未指定 service_name，将接受任何服务的任务", file=sys.stderr)

    # 健康检查 Consul
    ok, msg = consul_health_check()
    if not ok:
        die(f"Consul 不可达: {msg}", code=2)

    config = {
        "agent_id": agent_id,
        "service_name": service_name,
        "capabilities": capabilities,
        "repo_path": repo_path,
        "poll_interval": args.poll_interval,
        "task_timeout": args.task_timeout,
        "executor": args.executor.split() if args.executor else None,
        "execution_profiles": execution_profiles,
        "allowed_executables": allowed_executables,
        "reviewer": args.reviewer.split() if args.reviewer else None,
        "review_timeout": args.review_timeout,
        "worktree_base": args.worktree_base,
    }

    worker = Worker(config)

    if args.once:
        # 单次模式
        if not worker.register():
            sys.exit(2)
        worker.heartbeat.start()
        worker._tick()
        worker.heartbeat.stop()
        worker.deregister()
    else:
        # 持久化循环模式
        worker.start()


if __name__ == "__main__":
    main()
