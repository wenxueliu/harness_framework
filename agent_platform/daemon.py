"""
agent-platform daemon — 框架主进程

功能：在单个 Python 进程中并发运行 Aggregator、Watchdog、WebAPI 三大组件。
通过线程隔离，统一日志输出，单一信号即可优雅退出。

启动方式：
  python -m agent_platform.daemon                       # 默认配置
  python -m agent_platform.daemon --port 8080 \
    --consul 127.0.0.1:8500 --task-timeout 3600

退出：发送 SIGTERM 或 SIGINT (Ctrl+C)，三大组件协同退出。
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading

from .consul_client import ConsulClient
from .aggregator import Aggregator
from .watchdog import Watchdog
from .webapi import serve as webapi_serve


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    p = argparse.ArgumentParser(description="agent-platform 框架主进程")
    p.add_argument("--consul", default=os.environ.get("CONSUL_ADDR", "127.0.0.1:8500"))
    p.add_argument("--token", default=os.environ.get("CONSUL_TOKEN", ""))
    p.add_argument("--host", default="0.0.0.0", help="WebAPI 监听地址")
    p.add_argument("--port", type=int, default=8080, help="WebAPI 端口")
    p.add_argument("--aggregator-interval", type=int, default=5)
    p.add_argument("--watchdog-interval", type=int, default=30)
    p.add_argument("--task-timeout", type=int, default=120,
                   help="单个任务最长执行时间（秒）")
    p.add_argument("--heartbeat-timeout", type=int, default=120,
                   help="Agent 心跳超时（秒）")
    p.add_argument("--max-retry", type=int, default=3,
                   help="任务最大重试次数")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--no-aggregator", action="store_true")
    p.add_argument("--no-watchdog", action="store_true")
    p.add_argument("--no-webapi", action="store_true")
    args = p.parse_args()

    setup_logging(args.log_level)
    log = logging.getLogger("daemon")

    consul = ConsulClient(addr=args.consul, token=args.token)

    # 启动检查
    try:
        consul.kv_get("framework/healthcheck")
        log.info("Consul 连接成功: %s", args.consul)
    except Exception as e:
        log.error("Consul 连接失败: %s", e)
        sys.exit(2)
    consul.kv_put("framework/started_at", _now_iso())

    threads: list[threading.Thread] = []
    components = []

    # Aggregator
    if not args.no_aggregator:
        agg = Aggregator(consul, poll_interval=args.aggregator_interval)
        components.append(agg)
        t = threading.Thread(target=agg.run, name="aggregator", daemon=True)
        t.start()
        threads.append(t)

    # Watchdog
    if not args.no_watchdog:
        wd = Watchdog(consul, poll_interval=args.watchdog_interval,
                      task_timeout_seconds=args.task_timeout,
                      heartbeat_timeout=args.heartbeat_timeout,
                      max_retry=args.max_retry)
        components.append(wd)
        t = threading.Thread(target=wd.run, name="watchdog", daemon=True)
        t.start()
        threads.append(t)

    # WebAPI
    server = None
    if not args.no_webapi:
        server = webapi_serve(consul, host=args.host, port=args.port)
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

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info("agent-platform daemon 已启动，按 Ctrl+C 退出")
    try:
        stopping.wait()
    except KeyboardInterrupt:
        _stop("KeyboardInterrupt", None)

    log.info("daemon 退出完成")


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


if __name__ == "__main__":
    main()
