#!/usr/bin/env python3
"""
auto_register.py — Agent 自动注册到 Consul（包含心跳循环）

用法：
  # 一键启动后台运行（推荐）
  AGENT_ID="my-agent" python3 auto_register.py --capabilities "backend,translate" --daemon

  # 查看状态
  AGENT_ID="my-agent" python3 auto_register.py --status

  # 停止后台进程
  AGENT_ID="my-agent" python3 auto_register.py --stop

  # 前台运行（调试用）
  AGENT_ID="my-agent" python3 auto_register.py --capabilities "backend" --foreground

环境变量：
  AGENT_ID       全局唯一 Agent ID（必填）
  CONSUL_ADDR    Consul 地址，默认 127.0.0.1:8500

退出码：0 成功 / 1 运行中退出 / 2 系统错误
"""
import argparse
import os
import signal
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _consul import (
    env, service_register_safe, service_deregister_safe,
    health_check_pass_safe, kv_put, consul_health_check,
    emit_json, now_iso
)


# ── 路径配置 ──────────────────────────────────────────────────────────────────

def get_stage_bridge_dir() -> str:
    """获取 stage-bridge 配置目录"""
    home = os.path.expanduser("~/.claude/stage-bridge")
    os.makedirs(home, exist_ok=True)
    return home


def get_pid_file(agent_id: str) -> str:
    return os.path.join(get_stage_bridge_dir(), f"{agent_id}.pid")


def get_log_file(agent_id: str) -> str:
    return os.path.join(get_stage_bridge_dir(), f"{agent_id}.log")


# ── 进程管理 ──────────────────────────────────────────────────────────────────

def write_pid(pid: int, agent_id: str) -> None:
    """写入 PID 文件"""
    with open(get_pid_file(agent_id), "w") as f:
        f.write(str(pid))


def read_pid(agent_id: str) -> int | None:
    """读取 PID 文件"""
    try:
        with open(get_pid_file(agent_id), "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def remove_pid(agent_id: str) -> None:
    """删除 PID 文件"""
    try:
        os.remove(get_pid_file(agent_id))
    except FileNotFoundError:
        pass


def is_process_running(pid: int) -> bool:
    """检查进程是否在运行"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def log(agent_id: str, msg: str) -> None:
    """写入日志"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_file = get_log_file(agent_id)
    with open(log_file, "a") as f:
        f.write(f"[{timestamp}] {msg}\n")


# ── Agent 会话 ────────────────────────────────────────────────────────────────

class AgentSession:
    """管理 Agent 注册生命周期"""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.running = True
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5

    def register(self, capabilities: list, service_name: str, repo_path: str,
                 max_concurrent: int = 1, version: str = "1.0.0",
                 environment: str = "local") -> bool:
        """注册 Agent 到 Consul"""
        tags = [f"capability={c}" for c in capabilities]
        tags.append(f"env={environment}")
        tags.append(f"version={version}")
        if service_name:
            tags.append(f"service={service_name}")

        payload = {
            "ID": self.agent_id,
            "Name": "agent-worker",
            "Tags": tags,
            "Meta": {
                "agent_id": self.agent_id,
                "capabilities": ",".join(capabilities),
                "max_concurrent": str(max_concurrent),
                "current_load": "0",
                "service_name": service_name,
                "repo_path": repo_path,
                "registered_at": now_iso(),
            },
            "Check": {
                "CheckID": f"service:{self.agent_id}",
                "Name": f"TTL check for {self.agent_id}",
                "TTL": "30s",
                "DeregisterCriticalServiceAfter": "2m",
            },
        }

        success, msg = service_register_safe(payload)
        if success:
            kv_put(f"agents/{self.agent_id}/load", "0")
            kv_put(f"agents/{self.agent_id}/registered_at", now_iso())
            if service_name:
                kv_put(f"agents/{self.agent_id}/service", service_name)
        return success

    def heartbeat(self) -> bool:
        """发送心跳"""
        success, msg = health_check_pass_safe(f"service:{self.agent_id}", note="alive")
        if success:
            self.consecutive_errors = 0
        else:
            self.consecutive_errors += 1
        return success

    def deregister(self) -> bool:
        """注销 Agent"""
        return service_deregister_safe(self.agent_id)[0]

    def stop(self):
        """停止运行"""
        self.running = False


# ── 守护进程 ──────────────────────────────────────────────────────────────────

def become_daemon(agent_id: str) -> None:
    """创建守护进程"""
    # 第一次 fork
    try:
        pid = os.fork()
        if pid > 0:
            # 父进程：输出 PID 并退出
            print(json.dumps({
                "ok": True,
                "agent_id": agent_id,
                "pid": pid,
                "mode": "daemon",
                "pid_file": get_pid_file(agent_id),
                "log_file": get_log_file(agent_id),
            }))
            sys.exit(0)
    except OSError as e:
        sys.stderr.write(f"[auto_register:error] 第一次 fork 失败: {e}\n")
        sys.exit(1)

    # 脱离终端
    os.setsid()

    # 第二次 fork
    try:
        pid = os.fork()
        if pid > 0:
            # 第一个子进程：退出
            sys.exit(0)
    except OSError as e:
        sys.stderr.write(f"[auto_register:error] 第二次 fork 失败: {e}\n")
        sys.exit(1)

    # 重定向标准输入/输出/错误
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)


def daemon_loop(agent_id: str, capabilities: list, service_name: str, repo_path: str,
                max_concurrent: int, version: str, environment: str,
                heartbeat_interval: int) -> None:
    """守护进程主循环"""
    log(agent_id, f"启动守护进程 {os.getpid()}")

    # 创建会话
    session = AgentSession(agent_id)

    # 注册信号处理
    def signal_handler(signum, frame):
        log(agent_id, f"收到信号 {signum}，正在注销...")
        session.stop()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # 注册 Agent
    log(agent_id, f"注册 Agent: {agent_id}")
    success = session.register(
        capabilities=capabilities,
        service_name=service_name,
        repo_path=repo_path,
        max_concurrent=max_concurrent,
        version=version,
        environment=environment,
    )

    if not success:
        log(agent_id, "注册失败，退出")
        sys.exit(2)

    log(agent_id, f"注册成功，开始心跳循环（间隔 {heartbeat_interval}s）")

    # 心跳循环
    while session.running:
        try:
            ok = session.heartbeat()
            if not ok:
                log(agent_id, f"心跳失败 (连续 {session.consecutive_errors} 次)")
            elif session.consecutive_errors == 0:
                log(agent_id, "心跳成功")

            if session.consecutive_errors >= session.max_consecutive_errors:
                log(agent_id, f"连续 {session.max_consecutive_errors} 次心跳失败，退出")
                break

            time.sleep(heartbeat_interval)
        except Exception as e:
            log(agent_id, f"循环异常: {e}")
            break

    # 注销
    log(agent_id, f"注销 Agent: {agent_id}")
    session.deregister()
    log(agent_id, "完成")
    remove_pid(agent_id)


# ── 命令处理 ──────────────────────────────────────────────────────────────────

def cmd_status(agent_id: str) -> None:
    """查看运行状态"""
    pid = read_pid(agent_id)
    if pid is None:
        emit_json({"ok": False, "agent_id": agent_id, "status": "not_running", "pid": None})
        return

    running = is_process_running(pid)
    emit_json({
        "ok": True,
        "agent_id": agent_id,
        "status": "running" if running else "stale_pid",
        "pid": pid,
        "pid_file": get_pid_file(agent_id),
        "log_file": get_log_file(agent_id),
    })


def cmd_stop(agent_id: str) -> None:
    """停止后台进程"""
    pid = read_pid(agent_id)
    if pid is None:
        emit_json({"ok": False, "agent_id": agent_id, "message": "PID 文件不存在"})
        return

    if not is_process_running(pid):
        emit_json({"ok": True, "agent_id": agent_id, "message": "进程已不存在，清理 PID 文件"})
        remove_pid(agent_id)
        return

    # 发送 SIGTERM
    try:
        os.kill(pid, signal.SIGTERM)
        # 等待进程退出
        for _ in range(10):
            time.sleep(0.5)
            if not is_process_running(pid):
                break
        else:
            # 强制杀死
            os.kill(pid, signal.SIGKILL)
            emit_json({"ok": True, "agent_id": agent_id, "message": "强制终止", "killed": True})
            return

        remove_pid(agent_id)
        emit_json({"ok": True, "agent_id": agent_id, "message": "已停止"})
    except OSError as e:
        emit_json({"ok": False, "agent_id": agent_id, "error": str(e)})


def cmd_start(args, agent_id: str, consul_addr: str) -> None:
    """启动后台进程"""
    # 检查是否已在运行
    existing_pid = read_pid(agent_id)
    if existing_pid and is_process_running(existing_pid):
        emit_json({
            "ok": False,
            "agent_id": agent_id,
            "error": "Agent 已在运行",
            "pid": existing_pid,
        })
        sys.exit(1)

    # 检查 Consul 连接
    consul_ok, consul_msg = consul_health_check()
    if not consul_ok:
        emit_json({"ok": False, "agent_id": agent_id, "error": f"Consul 连接失败: {consul_msg}"})
        sys.exit(2)

    # 清理旧 PID 文件
    remove_pid(agent_id)

    # 第一次 fork：父进程输出 PID 后退出
    try:
        pid = os.fork()
        if pid > 0:
            # 父进程：写入 PID 文件并输出 JSON
            write_pid(pid, agent_id)
            emit_json({
                "ok": True,
                "agent_id": agent_id,
                "pid": pid,
                "mode": "daemon",
                "consul": consul_msg,
            })
            sys.exit(0)
    except OSError as e:
        emit_json({"ok": False, "agent_id": agent_id, "error": f"fork 失败: {e}"})
        sys.exit(1)

    # 子进程：脱离终端并继续
    os.setsid()

    # 第二次 fork：确保完全脱离
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError:
        sys.exit(1)

    # 重定向标准输入/输出/错误
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)

    # 写入新 PID
    write_pid(os.getpid(), agent_id)
    log(agent_id, f"守护进程启动 {os.getpid()}")

    # 解析参数
    capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]
    service_name = args.service or env("SERVICE_NAME", "")
    repo_path = args.repo_path or env("REPO_PATH", "")

    # 创建会话并运行
    session = AgentSession(agent_id)

    def signal_handler(signum, frame):
        log(agent_id, f"收到信号 {signum}")
        session.stop()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # 注册
    success = session.register(
        capabilities=capabilities,
        service_name=service_name,
        repo_path=repo_path,
        max_concurrent=args.max_concurrent,
        version=args.agent_version,
        environment=args.environment,
    )

    if not success:
        log(agent_id, "注册失败，退出")
        sys.exit(2)

    log(agent_id, f"注册成功，心跳间隔 {args.heartbeat_interval}s")

    # 心跳循环
    while session.running:
        try:
            ok = session.heartbeat()
            if not ok:
                log(agent_id, f"心跳失败 (连续 {session.consecutive_errors} 次)")
            elif session.consecutive_errors == 0 and session.heartbeat() == False:
                log(agent_id, "心跳成功")

            if session.consecutive_errors >= session.max_consecutive_errors:
                log(agent_id, f"连续 {session.max_consecutive_errors} 次心跳失败，退出")
                break

            time.sleep(args.heartbeat_interval)
        except Exception as e:
            log(agent_id, f"循环异常: {e}")
            break

    # 注销
    log(agent_id, "注销 Agent")
    session.deregister()
    log(agent_id, "完成")
    remove_pid(agent_id)
    sys.exit(0)


def cmd_foreground(args, agent_id: str, consul_addr: str) -> None:
    """前台运行模式"""
    print(f"[auto_register] 检查 Consul 连接 ({consul_addr})...", file=sys.stderr)
    consul_ok, consul_msg = consul_health_check()
    if not consul_ok:
        print(f"[auto_register:error] Consul 连接失败: {consul_msg}", file=sys.stderr)
        sys.exit(2)
    print(f"[auto_register] Consul 连接正常: {consul_msg}", file=sys.stderr)

    session = AgentSession(agent_id)

    def signal_handler(signum, frame):
        print(f"\n[auto_register] 收到信号 {signum}，正在注销...", file=sys.stderr)
        session.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]
    service_name = args.service or env("SERVICE_NAME", "")
    repo_path = args.repo_path or env("REPO_PATH", "")

    print(f"[auto_register] 注册 Agent: {agent_id}", file=sys.stderr)
    success = session.register(
        capabilities=capabilities,
        service_name=service_name,
        repo_path=repo_path,
        max_concurrent=args.max_concurrent,
        version=args.agent_version,
        environment=args.environment,
    )

    if not success:
        print(f"[auto_register:error] 注册失败", file=sys.stderr)
        sys.exit(2)

    print(f"[auto_register] 注册成功，开始心跳循环（间隔 {args.heartbeat_interval}s）", file=sys.stderr)

    emit_json({
        "ok": True,
        "agent_id": agent_id,
        "capabilities": capabilities,
        "service": service_name,
        "heartbeat_interval": args.heartbeat_interval,
        "mode": "foreground",
    })

    while session.running:
        try:
            ok = session.heartbeat()
            if not ok:
                print(f"[heartbeat] 失败 (连续 {session.consecutive_errors} 次)", file=sys.stderr)

            if session.consecutive_errors >= session.max_consecutive_errors:
                print(f"[auto_register:error] 连续 {session.max_consecutive_errors} 次心跳失败，退出", file=sys.stderr)
                break

            time.sleep(args.heartbeat_interval)
        except Exception as e:
            print(f"[auto_register:error] 循环异常: {e}", file=sys.stderr)
            break

    print(f"[auto_register] 注销 Agent: {agent_id}", file=sys.stderr)
    session.deregister()
    print(f"[auto_register] 完成", file=sys.stderr)
    sys.exit(0 if not session.running else 1)


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agent 自动注册到 Consul")
    parser.add_argument("--capabilities",
                        help="逗号分隔，如 backend,translate（启动时必填）")
    parser.add_argument("--service", default="",
                        help="绑定的微服务名称")
    parser.add_argument("--max-concurrent", type=int, default=1,
                        help="最大并发任务数")
    parser.add_argument("--repo-path", default="",
                        help="代码仓库本地路径")
    parser.add_argument("--agent-version", default="1.0.0")
    parser.add_argument("--env", dest="environment", default="local")
    parser.add_argument("--heartbeat-interval", type=int, default=10,
                        help="心跳间隔秒数（默认 10）")
    parser.add_argument("--daemon", action="store_true",
                        help="后台运行模式（默认）")
    parser.add_argument("--foreground", action="store_true",
                        help="前台运行模式（调试用）")
    parser.add_argument("--status", action="store_true",
                        help="查看运行状态")
    parser.add_argument("--stop", action="store_true",
                        help="停止后台进程")
    args = parser.parse_args()

    # 获取必填参数
    agent_id = env("AGENT_ID", required=True)
    consul_addr = env("CONSUL_ADDR", "127.0.0.1:8500")

    # 处理控制命令
    if args.status:
        cmd_status(agent_id)
        return

    if args.stop:
        cmd_stop(agent_id)
        return

    # 启动命令需要 capabilities
    if not args.capabilities:
        print("[auto_register:error] --capabilities 参数必填，或使用 --status / --stop", file=sys.stderr)
        sys.exit(2)

    if args.foreground:
        cmd_foreground(args, agent_id, consul_addr)
    else:
        cmd_start(args, agent_id, consul_addr)


if __name__ == "__main__":
    main()
