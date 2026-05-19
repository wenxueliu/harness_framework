"""
FileStore — 基于 JSON 文件的纯本地 KV 存储

零网络依赖，使用 fcntl.flock 实现进程间并发安全。
接口与 ConsulClient / LocalStore 完全兼容（KVStore Protocol）。

Agent 通过 scripts/file_kv.py CLI 直接读写同一文件，
无需 HTTP 服务器。
"""
from __future__ import annotations

import base64
import fcntl
import json
import logging
import os
import time
from typing import Any, Optional

log = logging.getLogger("file_store")

DEFAULT_DATA_FILE = os.path.expanduser("~/.harness/file_store.json")


class FileStore:
    """线程/进程安全的文件 KV 存储。

    使用独立 .lock 文件 + fcntl.flock 实现并发控制。
    所有操作都先获取锁，保证 read-modify-write 原子性。
    """

    def __init__(self, data_file: str = DEFAULT_DATA_FILE,
                 heartbeat_timeout: int = 120):
        self._data_file = os.path.abspath(data_file)
        self._lock_file = self._data_file + ".lock"
        self._heartbeat_timeout = heartbeat_timeout

        # 确保文件存在
        self._ensure_exists()

    # ── 文件管理 ──────────────────────────────────────────────────────────

    def _ensure_exists(self) -> None:
        parent = os.path.dirname(self._data_file)
        os.makedirs(parent, exist_ok=True)

        # 创建 lock 文件
        if not os.path.exists(self._lock_file):
            with open(self._lock_file, "w") as f:
                pass

        # 创建数据文件
        if not os.path.exists(self._data_file):
            self._write_data(self._empty_state())

    def _empty_state(self) -> dict:
        return {
            "global_index": 100,
            "store": {},
            "heartbeats": {},
            "agent_services": {},
        }

    def _acquire_lock(self) -> int:
        """获取排他锁，返回文件描述符。"""
        fd = os.open(self._lock_file, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _release_lock(self, fd: int) -> None:
        """释放锁并关闭文件描述符。"""
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass

    def _read_data(self) -> dict:
        """读取完整数据（调用前需持有锁）。"""
        try:
            with open(self._data_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return self._empty_state()

    def _write_data(self, data: dict) -> None:
        """原子写入数据（调用前需持有锁）。

        使用 tmp + os.replace 保证写不损坏已有数据。
        """
        tmp = self._data_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._data_file)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ── 低级原子操作 ──────────────────────────────────────────────────────

    def _atomic_read_modify_write(self, modifier) -> Any:
        """获取锁 → 读取 → 修改 → 写入 → 释放锁。"""
        fd = self._acquire_lock()
        try:
            data = self._read_data()
            result = modifier(data)
            self._write_data(data)
            return result
        finally:
            self._release_lock(fd)

    # ── KV 操作 ───────────────────────────────────────────────────────────

    def kv_get(self, key: str, recurse: bool = False
               ) -> tuple[Optional[Any], int]:
        fd = self._acquire_lock()
        try:
            data = self._read_data()
            store = data["store"]
            global_idx = data["global_index"]

            if recurse:
                matches = []
                for k in sorted(store.keys()):
                    if k.startswith(key):
                        v, idx = store[k]
                        matches.append({
                            "Key": k,
                            "Value": base64.b64encode(
                                v.encode("utf-8")).decode("ascii"),
                            "ModifyIndex": idx,
                            "_decoded": v,
                        })
                if not matches:
                    return None, global_idx
                return matches, max(m["ModifyIndex"] for m in matches)
            else:
                entry = store.get(key)
                if entry is None:
                    return None, global_idx
                v, idx = entry
                return v, idx
        finally:
            self._release_lock(fd)

    def kv_put(self, key: str, value: str,
               cas: Optional[int] = None) -> bool:
        fd = self._acquire_lock()
        try:
            data = self._read_data()
            store = data["store"]

            if cas is not None:
                existing = store.get(key)
                current_idx = existing[1] if existing else 0
                if current_idx != cas:
                    return False  # CAS 冲突

            data["global_index"] += 1
            store[key] = (value, data["global_index"])
            self._write_data(data)
            return True
        finally:
            self._release_lock(fd)

    def kv_delete(self, key: str, recurse: bool = False) -> None:
        fd = self._acquire_lock()
        try:
            data = self._read_data()
            store = data["store"]
            if recurse:
                to_delete = [k for k in store if k.startswith(key)]
                for k in to_delete:
                    del store[k]
            else:
                store.pop(key, None)
            self._write_data(data)
        finally:
            self._release_lock(fd)

    def kv_blocking_get(self, key: str, index: int = 0,
                        wait: str = "30s", recurse: bool = False
                        ) -> tuple[Optional[Any], int]:
        wait_sec = _parse_wait(wait)
        deadline = time.time() + wait_sec
        first = True
        while first or time.time() < deadline:
            first = False
            v, new_idx = self.kv_get(key, recurse=recurse)
            if v is not None and new_idx != index:
                return v, new_idx
            time.sleep(0.5)
        return None, self._get_global_index()

    # ── Agent 服务/心跳 ───────────────────────────────────────────────────

    def record_heartbeat(self, agent_id: str) -> None:
        fd = self._acquire_lock()
        try:
            data = self._read_data()
            data["heartbeats"][agent_id] = time.time()
            self._write_data(data)
        finally:
            self._release_lock(fd)

    def register_service(self, payload: dict) -> None:
        agent_id = payload.get("ID", "")
        if not agent_id:
            return
        fd = self._acquire_lock()
        try:
            data = self._read_data()
            data["agent_services"][agent_id] = payload
            # 不自动心跳，与 Consul TTL check 语义一致
            self._write_data(data)
        finally:
            self._release_lock(fd)

    def deregister_service(self, agent_id: str) -> None:
        fd = self._acquire_lock()
        try:
            data = self._read_data()
            data["agent_services"].pop(agent_id, None)
            data["heartbeats"].pop(agent_id, None)
            self._write_data(data)
        finally:
            self._release_lock(fd)

    def list_services(self, service_name: str = "agent-worker"
                      ) -> list[dict]:
        fd = self._acquire_lock()
        try:
            data = self._read_data()
            now = time.time()
            result = []
            for agent_id, payload in data["agent_services"].items():
                svc_name = payload.get("Name", "")
                if svc_name and svc_name != service_name:
                    continue
                last_hb = data["heartbeats"].get(agent_id, 0)
                if now - last_hb > self._heartbeat_timeout:
                    continue
                result.append({
                    "Service": {
                        "ID": agent_id,
                        "Tags": payload.get("Tags", []),
                        "Meta": payload.get("Meta", {}),
                    },
                    "Checks": [
                        {"CheckID": f"service:{agent_id}",
                         "Status": "passing"}
                    ],
                })
            return result
        finally:
            self._release_lock(fd)

    # ── 工具方法 ──────────────────────────────────────────────────────────

    def _get_global_index(self) -> int:
        fd = self._acquire_lock()
        try:
            return self._read_data()["global_index"]
        finally:
            self._release_lock(fd)

    def flush(self) -> None:
        """文件存储无需 flush（每次写入已持久化）。"""
        pass

    @property
    def data_file(self) -> str:
        return self._data_file

    @property
    def lock_file(self) -> str:
        return self._lock_file


# ── CLI 接口（供 stage-bridge 脚本调用）──────────────────────────────────

def file_kv_cli():
    """file_kv.py 的命令行入口。

    用法：
      python file_kv.py get <key> [--recurse] [--data-file <path>]
      python file_kv.py put <key> <value> [--cas <index>] [--data-file <path>]
      python file_kv.py delete <key> [--recurse] [--data-file <path>]
      python file_kv.py blocking-get <key> [--index <n>] [--wait <s>] [--data-file <path>]
      python file_kv.py register <json-payload> [--data-file <path>]
      python file_kv.py deregister <agent-id> [--data-file <path>]
      python file_kv.py heartbeat <agent-id> [--data-file <path>]
      python file_kv.py list-services [--data-file <path>]
      python file_kv.py status-leader [--data-file <path>]
    """
    import argparse
    import sys

    p = argparse.ArgumentParser(description="FileStore KV CLI")
    p.add_argument("--data-file", default=DEFAULT_DATA_FILE,
                   help=f"数据文件路径（默认 {DEFAULT_DATA_FILE}）")
    sub = p.add_subparsers(dest="command", required=True)

    # get
    sp_get = sub.add_parser("get")
    sp_get.add_argument("key")
    sp_get.add_argument("--recurse", action="store_true")

    # put
    sp_put = sub.add_parser("put")
    sp_put.add_argument("key")
    sp_put.add_argument("value")
    sp_put.add_argument("--cas", type=int, default=None)

    # delete
    sp_del = sub.add_parser("delete")
    sp_del.add_argument("key")
    sp_del.add_argument("--recurse", action="store_true")

    # blocking-get
    sp_bg = sub.add_parser("blocking-get")
    sp_bg.add_argument("key")
    sp_bg.add_argument("--index", type=int, default=0)
    sp_bg.add_argument("--wait", default="30s")
    sp_bg.add_argument("--recurse", action="store_true")

    # register
    sp_reg = sub.add_parser("register")
    sp_reg.add_argument("payload")

    # deregister
    sp_dereg = sub.add_parser("deregister")
    sp_dereg.add_argument("agent_id")

    # heartbeat
    sp_hb = sub.add_parser("heartbeat")
    sp_hb.add_argument("agent_id")

    # list-services
    sub.add_parser("list-services")

    # status-leader (兼容 _consul.py 的 consul_health_check)
    sub.add_parser("status-leader")

    args = p.parse_args()

    store = FileStore(data_file=args.data_file)

    try:
        if args.command == "get":
            v, idx = store.kv_get(args.key, recurse=args.recurse)
            if v is None:
                sys.exit(1)  # 404
            if isinstance(v, list):
                print(json.dumps(v, ensure_ascii=False))
            else:
                # 模拟 Consul 格式：数组包裹
                print(json.dumps([{
                    "Key": args.key,
                    "Value": base64.b64encode(
                        v.encode("utf-8")).decode("ascii"),
                    "ModifyIndex": idx,
                }], ensure_ascii=False))

        elif args.command == "put":
            ok = store.kv_put(args.key, args.value, cas=args.cas)
            print("true" if ok else "false")
            sys.exit(0 if ok else 1)

        elif args.command == "delete":
            store.kv_delete(args.key, recurse=args.recurse)

        elif args.command == "blocking-get":
            v, idx = store.kv_blocking_get(
                args.key, index=args.index,
                wait=args.wait, recurse=args.recurse)
            if v is None:
                sys.exit(1)
            if isinstance(v, list):
                print(json.dumps(v, ensure_ascii=False))
            else:
                print(json.dumps([{
                    "Key": args.key,
                    "Value": base64.b64encode(
                        v.encode("utf-8")).decode("ascii"),
                    "ModifyIndex": idx,
                }], ensure_ascii=False))

        elif args.command == "register":
            payload = json.loads(args.payload)
            store.register_service(payload)

        elif args.command == "deregister":
            store.deregister_service(args.agent_id)

        elif args.command == "heartbeat":
            store.record_heartbeat(args.agent_id)

        elif args.command == "list-services":
            services = store.list_services()
            print(json.dumps(services, ensure_ascii=False))

        elif args.command == "status-leader":
            print('"127.0.0.1:8300"')

    except Exception as e:
        log.exception("file_kv CLI error: %s", e)
        sys.exit(2)


def _parse_wait(wait: str) -> float:
    """解析 Consul 格式的等待时间字符串。"""
    wait = wait.strip().lower()
    if wait.endswith("ms"):
        return float(wait[:-2]) / 1000
    if wait.endswith("s"):
        return float(wait[:-1])
    if wait.endswith("m"):
        return float(wait[:-1]) * 60
    if wait.endswith("h"):
        return float(wait[:-1]) * 3600
    try:
        return float(wait)
    except ValueError:
        return 30
