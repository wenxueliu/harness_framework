"""
harness-framework daemon — 框架主进程

功能：在单个 Python 进程中并发运行 ACPDispatcher、Aggregator、Watchdog、WebAPI。
通过线程隔离，统一日志输出，单一信号即可优雅退出。

启动方式：
  python -m harness_framework.daemon                       # 默认配置
  python -m harness_framework.daemon --port 8080 \
    --consul 127.0.0.1:8500 --task-timeout 3600

退出：发送 SIGTERM 或 SIGINT (Ctrl+C)，各组件协同退出。
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import signal
import sys
import threading
from typing import Any

from .consul_client import ConsulClient
from .aggregator import Aggregator
from .watchdog import Watchdog
from .webapi import serve as webapi_serve
from .run_manager import RunManager
from .acp_dispatcher import ACPDispatcher
from .auth import AuthConfig
from .capabilities import FeatureConfig
from .workspace_security import WorkspaceSecurity
from .workspace_manager import WorkspaceManager
from .project_groups import ProjectGroupService


def setup_logging(level: str, log_dir: str = "",
                  max_bytes: int = 10 * 1024 * 1024,
                  backup_count: int = 5) -> None:
    fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    handlers: list[logging.Handler] = []

    # stdout handler — 保持原有格式
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter(fmt=fmt, datefmt="%H:%M:%S"))
    handlers.append(stdout_handler)

    # file handler — 配置了 log_dir 时才启用，使用完整时间戳
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=os.path.join(log_dir, "harness-framework.log"),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
        file_handler.setFormatter(logging.Formatter(
            fmt=file_fmt, datefmt="%Y-%m-%d %H:%M:%S"
        ))
        handlers.append(file_handler)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=handlers,
    )


def main() -> None:
    p = argparse.ArgumentParser(description="harness-framework 框架主进程")
    p.add_argument("--consul", default=os.environ.get("CONSUL_ADDR", "127.0.0.1:8500"))
    p.add_argument("--token", default=os.environ.get("CONSUL_TOKEN", ""))
    p.add_argument("--host", default="0.0.0.0", help="WebAPI 监听地址")
    p.add_argument("--port", type=int, default=8080, help="WebAPI 端口")
    p.add_argument("--auth-mode", choices=("local", "trusted-proxy"),
                   default=os.environ.get("HARNESS_AUTH_MODE", "local"),
                   help="WebAPI 身份模式")
    p.add_argument("--local-user", default=os.environ.get(
        "HARNESS_LOCAL_USER", os.environ.get("USER", "local-user")),
        help="local 身份模式显示的用户")
    p.add_argument("--trusted-proxy", action="append", default=None,
                   help="允许提供身份头的反向代理地址，可重复")
    p.add_argument("--platform-owner", action="append", default=None,
                   help="允许创建项目组的平台管理员 subject，可重复")
    p.add_argument("--workspace-root", action="append", default=None,
                   metavar="ALIAS=PATH",
                   help="允许登记的 Workspace 根目录，可重复")
    p.add_argument("--workspace-provision-root", default="default",
                   help="创建 worktree/受控副本使用的 root alias")
    p.add_argument("--demo-mode", action="store_true",
                   help="显式启用 /tmp 临时演示工作区")
    p.add_argument("--git-host", action="append", default=None,
                   help="允许 GIT_CLONE Workspace 的 HTTPS host，可重复")
    p.add_argument("--aggregator-interval", type=int, default=5)
    p.add_argument("--watchdog-interval", type=int, default=30)
    p.add_argument("--task-timeout", type=int, default=120,
                   help="单个任务最长执行时间（秒）")
    p.add_argument("--heartbeat-timeout", type=int, default=120,
                   help="Agent 心跳超时（秒）")
    p.add_argument("--max-retry", type=int, default=3,
                   help="任务最大重试次数")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--log-dir", default="",
                   help="日志目录（为空仅输出到 stdout）")
    p.add_argument("--log-max-bytes", type=int, default=10 * 1024 * 1024,
                   help="单个日志文件最大大小，默认 10MB")
    p.add_argument("--log-backup-count", type=int, default=5,
                   help="保留的旧日志文件数量，默认 5")
    p.add_argument("--no-aggregator", action="store_true")
    p.add_argument("--no-watchdog", action="store_true")
    p.add_argument("--no-webapi", action="store_true")
    p.add_argument("--no-acp-dispatcher", action="store_true",
                   help="禁用 ACP 主动任务分派（兼容旧注册/抢占 Worker）")
    p.add_argument("--acp-claude-command",
                   default=os.environ.get(
                       "ACP_CLAUDE_COMMAND",
                       '["npx", "-y", "@agentclientprotocol/claude-agent-acp"]'),
                   help="Claude ACP adapter argv（JSON 数组）")
    p.add_argument("--acp-codex-command",
                   default=os.environ.get(
                       "ACP_CODEX_COMMAND",
                       '["npx", "-y", "@agentclientprotocol/codex-acp"]'),
                   help="Codex ACP adapter argv（JSON 数组）")
    p.add_argument("--acp-routing",
                   default=os.environ.get("ACP_TASK_ROUTING", "{}"),
                   help="task type 到 claude/codex 的覆盖映射（JSON 对象）")
    p.add_argument("--acp-workspace-root",
                   default=os.environ.get("ACP_WORKSPACE_ROOT", os.getcwd()),
                   help="ACP Agent 默认工作目录")
    p.add_argument("--acp-max-concurrency", type=int,
                   default=int(os.environ.get("ACP_MAX_CONCURRENCY", "4")))
    p.add_argument("--acp-task-timeout", type=int,
                   default=int(os.environ.get("ACP_TASK_TIMEOUT", "7200")),
                   help="单次 ACP prompt 最长执行时间（秒）")
    p.add_argument("--acp-permission-policy", choices=("allow_once", "deny"),
                   default=os.environ.get("ACP_PERMISSION_POLICY", "allow_once"))
    p.add_argument("--local", action="store_true",
                   help="使用本地内存存储替代 Consul（含嵌入式 HTTP 服务器）")
    p.add_argument("--local-port", type=int, default=8500,
                   help="本地模式 HTTP 服务器端口（默认 8500）")
    p.add_argument("--local-data-file", default="",
                   help="本地模式 JSON 持久化文件路径 "
                        "（默认 ~/.harness/local_store.json）")
    p.add_argument("--local-file", action="store_true",
                   help="纯文件模式：使用 JSON 文件存储，无 HTTP 服务器。"
                        "Agent 通过 scripts/file_kv.py 直接读写文件")
    p.add_argument("--standalone", action="store_true",
                   help="单机模式：Agent 无需注册/注销/心跳，"
                        "框架提供默认 Agent ID 始终视为存活")
    p.add_argument("--standalone-agent-id", default="standalone-agent",
                   help="单机模式的默认 Agent ID（默认 standalone-agent）")
    args = p.parse_args()

    setup_logging(args.log_level, log_dir=args.log_dir,
                  max_bytes=args.log_max_bytes,
                  backup_count=args.log_backup_count)
    log = logging.getLogger("daemon")

    local_store: Any = None
    local_server: Any = None

    if args.local_file:
        # 纯文件模式：FileStore，无 HTTP 服务器，自动启用单机模式
        args.standalone = True
        from .file_store import FileStore, DEFAULT_DATA_FILE
        data_file = args.local_data_file or DEFAULT_DATA_FILE
        consul = FileStore(data_file=data_file,
                           heartbeat_timeout=args.heartbeat_timeout)
        local_store = consul
        log.info("FileStore 已启动 (data=%s, 无 HTTP 服务器)", data_file)
        log.info("Agent 请使用 scripts/file_kv.py --data-file '%s' 操作 KV", data_file)
    elif args.local:
        from .local_store import LocalStore, start_local_consul_server
        data_file = args.local_data_file or os.path.expanduser(
            "~/.harness/local_store.json")
        consul = LocalStore(data_file=data_file,
                            heartbeat_timeout=args.heartbeat_timeout)
        local_store = consul
        local_server, _ = start_local_consul_server(
            consul, host="0.0.0.0", port=args.local_port)
        log.info("LocalStore 已启动 (HTTP on 0.0.0.0:%d, data=%s)",
                 args.local_port, data_file)
    else:
        consul = ConsulClient(addr=args.consul, token=args.token)

        # 启动检查
        try:
            consul.kv_get("framework/healthcheck")
            log.info("Consul 连接成功: %s", args.consul)
        except Exception as e:
            log.error("Consul 连接失败: %s", e)
            sys.exit(2)
        consul.kv_put("framework/started_at", _now_iso())

    # 共享的 RunManager 实例
    run_manager = RunManager(consul)
    root_values = args.workspace_root or [f"default={args.acp_workspace_root}"]
    if args.demo_mode and not any(value.startswith("demo=") for value in root_values):
        root_values = [*root_values, "demo=/tmp"]
    workspace_roots = _parse_workspace_roots(root_values)
    workspace_security = WorkspaceSecurity(workspace_roots)
    project_groups = ProjectGroupService(consul)
    workspace_manager = WorkspaceManager(
        consul, workspace_security, project_groups,
        provision_root_alias=args.workspace_provision_root,
        demo_mode=args.demo_mode, demo_root_alias="demo",
        allowed_git_hosts=tuple(args.git_host or ()),
    )
    run_manager.workspace_manager = workspace_manager

    if args.standalone:
        log.info("单机模式已启用，默认 Agent ID: %s（无需注册/心跳）",
                 args.standalone_agent_id)

    threads: list[threading.Thread] = []
    components = []

    # Aggregator
    if not args.no_aggregator:
        agg = Aggregator(consul, run_manager=run_manager,
                         poll_interval=args.aggregator_interval)
        components.append(agg)
        t = threading.Thread(target=agg.run, name="aggregator", daemon=True)
        t.start()
        threads.append(t)

    # ACP task dispatcher.  This is the primary execution path: PENDING tasks
    # are claimed centrally and an ACP adapter is created for that step.
    if not args.no_acp_dispatcher:
        try:
            commands = {
                "claude": _json_argv(args.acp_claude_command, "--acp-claude-command"),
                "codex": _json_argv(args.acp_codex_command, "--acp-codex-command"),
            }
            routing = json.loads(args.acp_routing)
            if not isinstance(routing, dict) or not all(
                isinstance(k, str) and v in {"claude", "codex"}
                for k, v in routing.items()
            ):
                raise ValueError("--acp-routing must map task types to claude or codex")
        except (json.JSONDecodeError, ValueError) as exc:
            p.error(str(exc))
        acp_dispatcher = ACPDispatcher(
            consul, run_manager, commands=commands, routing=routing,
            workspace_root=args.acp_workspace_root,
            poll_interval=min(float(args.aggregator_interval), 1.0),
            task_timeout=args.acp_task_timeout,
            max_concurrency=args.acp_max_concurrency,
            permission_policy=args.acp_permission_policy,
            workspace_manager=workspace_manager,
        )
        components.append(acp_dispatcher)
        t = threading.Thread(
            target=acp_dispatcher.run, name="acp-dispatcher", daemon=True
        )
        t.start()
        threads.append(t)

    # Watchdog
    if not args.no_watchdog:
        wd = Watchdog(consul, run_manager=run_manager,
                      poll_interval=args.watchdog_interval,
                      task_timeout_seconds=args.task_timeout,
                      heartbeat_timeout=args.heartbeat_timeout,
                      max_retry=args.max_retry,
                      standalone=args.standalone,
                      default_agent_id=args.standalone_agent_id)
        components.append(wd)
        t = threading.Thread(target=wd.run, name="watchdog", daemon=True)
        t.start()
        threads.append(t)

    # WebAPI
    server = None
    if not args.no_webapi:
        trusted_proxies = frozenset(args.trusted_proxy or ["127.0.0.1", "::1"])
        auth_config = AuthConfig(
            mode=args.auth_mode,
            local_user=args.local_user,
            trusted_proxy_addresses=trusted_proxies,
            platform_owners=frozenset(args.platform_owner or []),
        )
        server = webapi_serve(consul, host=args.host, port=args.port,
                              run_manager=run_manager,
                              auth_config=auth_config,
                              features=FeatureConfig({
                                  "project_groups": True,
                                  "workspace_browse": True,
                                  "workspace_write": True,
                                  "workspace_diff": True,
                                  "sse_events": True,
                              }),
                              workspace_security=workspace_security,
                              workspace_manager=workspace_manager)
        t = threading.Thread(target=server.serve_forever, name="webapi", daemon=True)
        t.start()
        threads.append(t)

    # 信号处理
    stopping = threading.Event()

    def _stop(signum, _frame):
        log.info("收到信号 %s，开始优雅退出...", signum)
        stopping.set()
        for c in components:
            try:
                c.stop()
            except Exception:
                pass
        if server:
            threading.Thread(target=server.shutdown, daemon=True).start()
        if local_store:
            try:
                local_store.flush()
            except Exception:
                pass
        if local_server:
            threading.Thread(target=local_server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info("harness-framework daemon 已启动，按 Ctrl+C 退出")
    try:
        stopping.wait()
    except KeyboardInterrupt:
        _stop("KeyboardInterrupt", None)

    log.info("daemon 退出完成")


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


def _json_argv(raw: str, option: str) -> list[str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{option} must be a JSON argv array") from exc
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{option} must be a non-empty JSON argv array")
    return value


def _parse_workspace_roots(values: list[str]) -> dict[str, str]:
    roots: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--workspace-root must use ALIAS=PATH")
        alias, path = value.split("=", 1)
        alias, path = alias.strip(), os.path.abspath(path.strip())
        if not alias or not path.strip():
            raise ValueError("--workspace-root must use non-empty ALIAS=PATH")
        if alias in roots:
            raise ValueError(f"duplicate workspace root alias: {alias}")
        roots[alias] = path
    return roots


if __name__ == "__main__":
    main()
