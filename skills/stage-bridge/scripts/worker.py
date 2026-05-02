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
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (  # noqa: E402
    env, kv_get, kv_put, kv_delete, emit_json, die, now_iso,
    consul_health_check, service_register_safe, service_deregister_safe,
    health_check_pass_safe,
)

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


def load_context(req_id: str) -> dict:
    """加载需求级上下文。"""
    items, _ = kv_get(f"workflows/{req_id}/context", recurse=True)
    ctx = {}
    if items:
        prefix = f"workflows/{req_id}/context/"
        for it in items:
            k = it["Key"].split(prefix, 1)[-1] if prefix in it["Key"] else it["Key"]
            ctx[k] = it.get("_decoded", "")
    return ctx


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
    kv_put(f"{base}/assigned_agent", agent_id)
    kv_put(f"{base}/started_at", ts)
    kv_put(f"{base}/worker_pid", str(os.getpid()))

    # 4. 读取完整上下文
    task_meta = load_task_meta(req_id, task_name)
    context = load_context(req_id)

    return True, {
        "req_id": req_id,
        "task_name": task_name,
        "task_meta": task_meta,
        "context": context,
    }


def complete_task(req_id: str, task_name: str, agent_id: str,
                  result: dict = None) -> None:
    """标记任务完成。"""
    base = f"workflows/{req_id}/tasks/{task_name}"
    ts = now_iso()
    kv_put(f"{base}/status", "DONE")
    kv_put(f"{base}/completed_by", agent_id)
    kv_put(f"{base}/completed_at", ts)
    if result:
        kv_put(f"{base}/result", json.dumps(result, ensure_ascii=False))


def fail_task(req_id: str, task_name: str, agent_id: str,
              error: str, retry_hint: str = "retry") -> None:
    """标记任务失败。"""
    base = f"workflows/{req_id}/tasks/{task_name}"
    ts = now_iso()
    kv_put(f"{base}/status", "FAILED")
    kv_put(f"{base}/failed_by", agent_id)
    kv_put(f"{base}/failed_at", ts)
    kv_put(f"{base}/error_message", error)
    kv_put(f"{base}/retry_hint", retry_hint)


def log_step(req_id: str, task_name: str, agent_id: str,
             step_type: str, message: str) -> None:
    """记录执行步骤到会话流。"""
    session_id = f"{agent_id}-{int(time.time())}"
    ts = now_iso()
    seq = str(int(time.time() * 1000))
    base = f"workflows/{req_id}/sessions/{task_name}/{session_id}/events/{seq}"
    kv_put(f"{base}/type", step_type)
    kv_put(f"{base}/message", message)
    kv_put(f"{base}/timestamp", ts)
    kv_put(f"{base}/agent", agent_id)


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
    executor = config.get("executor")

    log_step(req_id, task_name, agent_id, "EXEC_START",
             f"开始执行 {task_name} (type={task_meta.get('type')})")

    if not executor:
        # 无 executor：占位模式，标记为 DONE（等待 step 3 适配 TDD executor）
        print(f"[worker] 无 executor，任务 {task_name} 标记为 DONE（占位模式）", file=sys.stderr)
        log_step(req_id, task_name, agent_id, "EXEC_END",
                 f"占位模式完成 {task_name}")
        return {"status": "DONE", "mode": "placeholder", "message": "无 executor，跳过实际执行"}

    # 构建 executor 输入
    task_input = {
        "req_id": req_id,
        "task_name": task_name,
        "task_meta": task_meta,
        "context": context,
        "config": {
            "agent_id": agent_id,
            "service_name": config.get("service_name", ""),
            "repo_path": config.get("repo_path", ""),
            "worktree_base": config.get("worktree_base", ".worktree"),
        },
    }

    try:
        input_json = json.dumps(task_input, ensure_ascii=False)
        proc = subprocess.run(
            executor,
            input=input_json,
            capture_output=True,
            text=True,
            timeout=config.get("task_timeout", 7200),  # 默认 2h
        )

        if proc.returncode == 0:
            try:
                result = json.loads(proc.stdout)
            except json.JSONDecodeError:
                result = {"status": "DONE", "stdout": proc.stdout}
            log_step(req_id, task_name, agent_id, "EXEC_END",
                     f"任务执行成功: {task_name}")
            return result
        else:
            log_step(req_id, task_name, agent_id, "EXEC_ERROR",
                     f"executor 退出码 {proc.returncode}: {proc.stderr[:500]}")
            return {
                "status": "FAILED",
                "exit_code": proc.returncode,
                "stderr": proc.stderr[:1000],
            }

    except subprocess.TimeoutExpired:
        log_step(req_id, task_name, agent_id, "EXEC_TIMEOUT",
                 f"任务超时: {task_name}")
        return {"status": "FAILED", "error": "task_timeout"}
    except Exception as e:
        log_step(req_id, task_name, agent_id, "EXEC_ERROR",
                 f"executor 异常: {e}")
        return {"status": "FAILED", "error": str(e)}


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
        })

        print(f"[worker] 开始执行: {req_id}/{task_name} "
              f"(type={meta.get('type')}, service={meta.get('service_name')})")

        # 检查 ABORT
        ctl = check_control(req_id)
        if ctl == "ABORT":
            fail_task(req_id, task_name, self.agent_id,
                      "任务被 ABORT", retry_hint="manual")
            self._clear_current()
            return

        # 执行任务
        context = load_context(req_id)
        exec_result = execute_task(req_id, task_name, meta, context, self.config)

        # 5. 报告结果
        if exec_result.get("status") == "DONE":
            complete_task(req_id, task_name, self.agent_id, exec_result)
            print(f"[worker] 任务完成: {req_id}/{task_name}")
        else:
            error = exec_result.get("error", exec_result.get("stderr", "未知错误"))
            fail_task(req_id, task_name, self.agent_id, error)
            print(f"[worker] 任务失败: {req_id}/{task_name}: {error[:200]}")

        self._clear_current()

    def _clear_current(self):
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
