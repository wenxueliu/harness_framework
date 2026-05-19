"""
LocalStore — 本地内存 KV 存储，替代 Consul

提供与 ConsulClient 完全相同的接口，外加：
- 嵌入式 HTTP 服务器，实现 Consul v1 API 子集
- 可选的 JSON 文件持久化
- Agent 心跳跟踪（替代 Consul Health Check）

零外部依赖，仅使用 Python 标准库。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("local_store")


# ── LocalStore ──────────────────────────────────────────────────────────────

class LocalStore:
    """线程安全的内存 KV 存储，接口与 ConsulClient 兼容。"""

    def __init__(self, data_file: Optional[str] = None,
                 heartbeat_timeout: int = 120):
        self._lock = threading.RLock()
        # key -> (decoded_value, modify_index)
        self._store: dict[str, tuple[str, int]] = {}
        self._global_index: int = 100  # 单调递增

        # Agent 心跳和服务注册
        self._heartbeats: dict[str, float] = {}  # agent_id -> last_heartbeat_epoch
        self._agent_services: dict[str, dict] = {}  # agent_id -> registration payload

        self._heartbeat_timeout = heartbeat_timeout

        # 持久化
        self._data_file = data_file
        self._dirty = False
        if data_file and os.path.exists(data_file):
            self._load()

        # 定期自动保存线程
        self._save_thread: Optional[threading.Thread] = None
        self._save_stop = threading.Event()
        if data_file:
            self._start_auto_save()

    # ── 持久化 ───────────────────────────────────────────────────────────────

    def _start_auto_save(self) -> None:
        """启动定期保存线程（每 5 秒检查一次脏标记）。"""
        def _auto_save():
            while not self._save_stop.wait(5.0):
                if self._dirty:
                    self._save()
        self._save_thread = threading.Thread(
            target=_auto_save, name="localstore-autosave", daemon=True)
        self._save_thread.start()

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _save(self) -> None:
        if not self._data_file:
            return
        with self._lock:
            data = {
                "global_index": self._global_index,
                "store": {k: [v, idx] for k, (v, idx) in self._store.items()},
                "heartbeats": {k: v for k, v in self._heartbeats.items()},
                "agent_services": dict(self._agent_services),
            }
        try:
            parent = os.path.dirname(os.path.abspath(self._data_file))
            os.makedirs(parent, exist_ok=True)
            tmp = self._data_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._data_file)  # 原子重命名
            self._dirty = False
        except Exception:
            log.exception("LocalStore save failed")

    def _load(self) -> None:
        try:
            with open(self._data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._global_index = data.get("global_index", 100)
            for k, v in data.get("store", {}).items():
                if isinstance(v, list) and len(v) == 2:
                    self._store[k] = (v[0], v[1])
                else:
                    # 兼容旧格式
                    self._store[k] = (str(v), self._global_index)
            self._heartbeats = {k: float(v) for k, v in
                                data.get("heartbeats", {}).items()}
            self._agent_services = data.get("agent_services", {})
            log.info("LocalStore loaded from %s (%d keys)", self._data_file,
                     len(self._store))
        except Exception:
            log.exception("LocalStore load failed, starting fresh")

    def flush(self) -> None:
        """强制保存到文件（关闭前调用）。"""
        self._save_stop.set()
        self._save()

    # ── KV 操作 ──────────────────────────────────────────────────────────────

    def kv_get(self, key: str, recurse: bool = False
               ) -> tuple[Optional[Any], int]:
        with self._lock:
            if recurse:
                matches = []
                for k in sorted(self._store.keys()):
                    if k.startswith(key):
                        v, idx = self._store[k]
                        matches.append({
                            "Key": k,
                            "Value": base64.b64encode(
                                v.encode("utf-8")).decode("ascii"),
                            "ModifyIndex": idx,
                            "_decoded": v,
                        })
                if not matches:
                    return None, self._global_index
                return matches, max(m["ModifyIndex"] for m in matches)
            else:
                entry = self._store.get(key)
                if entry is None:
                    return None, self._global_index
                v, idx = entry
                return v, idx

    def kv_put(self, key: str, value: str,
               cas: Optional[int] = None) -> bool:
        with self._lock:
            if cas is not None:
                existing = self._store.get(key)
                current_idx = existing[1] if existing else 0
                if current_idx != cas:
                    return False  # CAS 冲突

            self._global_index += 1
            self._store[key] = (value, self._global_index)
            self._mark_dirty()
            return True

    def kv_delete(self, key: str, recurse: bool = False) -> None:
        with self._lock:
            if recurse:
                to_delete = [k for k in self._store if k.startswith(key)]
                for k in to_delete:
                    del self._store[k]
            else:
                self._store.pop(key, None)
            self._mark_dirty()

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
        # 超时
        return None, self._global_index

    # ── Agent 服务注册/心跳 ──────────────────────────────────────────────────

    def record_heartbeat(self, agent_id: str) -> None:
        with self._lock:
            self._heartbeats[agent_id] = time.time()

    def register_service(self, payload: dict) -> None:
        agent_id = payload.get("ID", "")
        if not agent_id:
            return
        with self._lock:
            self._agent_services[agent_id] = payload
            # 不自动记录心跳，与 Consul TTL check 语义一致：
            # 注册后 check 处于 critical 状态，需要显式 heartbeat 才能变为 passing

    def deregister_service(self, agent_id: str) -> None:
        with self._lock:
            self._agent_services.pop(agent_id, None)
            self._heartbeats.pop(agent_id, None)

    def list_services(self, service_name: str = "agent-worker"
                      ) -> list[dict]:
        now = time.time()
        with self._lock:
            result = []
            for agent_id, payload in self._agent_services.items():
                # 检查服务名称（默认为 agent-worker）
                svc_name = payload.get("Name", "")
                if svc_name and svc_name != service_name:
                    continue
                last_hb = self._heartbeats.get(agent_id, 0)
                if now - last_hb > self._heartbeat_timeout:
                    continue  # 心跳超时，视为死亡
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


# ── LocalConsulHandler ──────────────────────────────────────────────────────

class LocalConsulHandler(BaseHTTPRequestHandler):
    """实现 Consul v1 API 子集的 HTTP 处理器。

    类属性 store 必须在启动前设置。
    """
    store: LocalStore = None  # type: ignore[assignment]

    def log_message(self, format, *args):
        log.debug("%s - %s", self.address_string(), format % args)

    # ── 请求分发 ──────────────────────────────────────────────────────────

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        try:
            if path.startswith("/v1/kv/"):
                key = path[len("/v1/kv/"):]
                return self._handle_kv_get(key)
            if path.startswith("/v1/health/service/"):
                name = path[len("/v1/health/service/"):]
                return self._handle_health_service(name)
            if path == "/v1/status/leader":
                return self._handle_status_leader()
            if path == "/v1/agent/self" or path == "/v1/agent/self/":
                return self._handle_agent_self()
            self._send_json(404, "not found")
        except Exception:
            log.exception("GET %s failed", self.path)
            self._send_text(500, "internal error")

    def do_PUT(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        try:
            if path.startswith("/v1/kv/"):
                key = path[len("/v1/kv/"):]
                return self._handle_kv_put(key, body)
            if path == "/v1/agent/service/register":
                return self._handle_service_register(body)
            if path.startswith("/v1/agent/service/deregister/"):
                agent_id = path[len("/v1/agent/service/deregister/"):]
                return self._handle_service_deregister(agent_id)
            if path.startswith("/v1/agent/check/pass/"):
                check_id = path[len("/v1/agent/check/pass/"):]
                return self._handle_check_pass(check_id)
            self._send_json(404, "not found")
        except Exception:
            log.exception("PUT %s failed", self.path)
            self._send_text(500, "internal error")

    def do_DELETE(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        try:
            if path.startswith("/v1/kv/"):
                key = path[len("/v1/kv/"):]
                return self._handle_kv_delete(key)
            self._send_json(404, "not found")
        except Exception:
            log.exception("DELETE %s failed", self.path)
            self._send_text(500, "internal error")

    # ── KV GET ────────────────────────────────────────────────────────────

    def _handle_kv_get(self, key: str):
        params = self._parse_qs()
        recurse = params.get("recurse") == "true"
        wait = params.get("wait")
        index = int(params.get("index", 0))

        if wait:
            value, new_index = self.store.kv_blocking_get(
                key, index, wait, recurse)
        else:
            value, new_index = self.store.kv_get(key, recurse=recurse)

        if value is None:
            self.send_response(404)
            self.send_header("X-Consul-Index", str(new_index))
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Consul-Index", str(new_index))
        if recurse and isinstance(value, list):
            # value 已经是完整的 KV 条目列表
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        elif isinstance(value, str):
            # 单个值：包装为列表
            body = json.dumps([{
                "Key": key,
                "Value": base64.b64encode(
                    value.encode("utf-8")).decode("ascii"),
                "ModifyIndex": new_index,
            }], ensure_ascii=False).encode("utf-8")
        else:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── KV PUT ────────────────────────────────────────────────────────────

    def _handle_kv_put(self, key: str, body: bytes):
        params = self._parse_qs()
        cas_str = params.get("cas")
        cas = int(cas_str) if cas_str else None
        value = body.decode("utf-8") if body else ""

        success = self.store.kv_put(key, value, cas=cas)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        result = b"true" if success else b"false"
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)

    # ── KV DELETE ─────────────────────────────────────────────────────────

    def _handle_kv_delete(self, key: str):
        params = self._parse_qs()
        recurse = params.get("recurse") == "true"
        self.store.kv_delete(key, recurse=recurse)
        self._send_text(200, "")

    # ── Health Service ────────────────────────────────────────────────────

    def _handle_health_service(self, name: str):
        services = self.store.list_services(name)
        self._send_json(200, services)

    # ── Service Register / Deregister ─────────────────────────────────────

    def _handle_service_register(self, body: bytes):
        try:
            payload = json.loads(body) if body else {}
            self.store.register_service(payload)
            self._send_text(200, "")
        except json.JSONDecodeError:
            self._send_text(400, "invalid JSON")

    def _handle_service_deregister(self, agent_id: str):
        self.store.deregister_service(agent_id)
        self._send_text(200, "")

    # ── Check Pass (Heartbeat) ────────────────────────────────────────────

    def _handle_check_pass(self, check_id: str):
        # check_id 格式: "service:<agent_id>" 或直接是 agent_id
        agent_id = check_id
        if check_id.startswith("service:"):
            agent_id = check_id[len("service:"):]

        # 解析 note 参数（可选）
        params = self._parse_qs()
        note = params.get("note", "")
        if note:
            log.debug("heartbeat from %s: %s", agent_id, note)

        self.store.record_heartbeat(agent_id)
        self._send_text(200, "")

    # ── Status Leader ─────────────────────────────────────────────────────

    def _handle_status_leader(self):
        # 返回一个伪装 leader 字符串（Consul 格式）
        self._send_text(200, '"127.0.0.1:8300"')

    # ── Agent Self ────────────────────────────────────────────────────────

    def _handle_agent_self(self):
        self._send_json(200, {
            "Config": {
                "Datacenter": "local",
                "NodeName": "harness-local",
                "Server": True,
            },
            "Member": {
                "Name": "harness-local",
                "Addr": "127.0.0.1",
                "Port": 8500,
            },
        })

    # ── 工具方法 ──────────────────────────────────────────────────────────

    def _parse_qs(self) -> dict[str, str]:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return {k: v[0] for k, v in qs.items()}

    def _send_text(self, code: int, text: str):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ── Server Startup ──────────────────────────────────────────────────────────

def start_local_consul_server(store: LocalStore, host: str = "0.0.0.0",
                              port: int = 8500
                              ) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """启动嵌入式 Consul 兼容 HTTP 服务器。"""
    LocalConsulHandler.store = store
    server = ThreadingHTTPServer((host, port), LocalConsulHandler)
    thread = threading.Thread(
        target=server.serve_forever, name="local-consul", daemon=True)
    thread.start()
    log.info("Local Consul HTTP server listening on %s:%d", host, port)
    return server, thread


# ── Helpers ─────────────────────────────────────────────────────────────────

def _parse_wait(wait: str) -> float:
    """解析 Consul 格式的等待时间字符串。

    "30s" -> 30, "1m" -> 60, "1h" -> 3600
    """
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
        return 30  # 默认 30 秒
