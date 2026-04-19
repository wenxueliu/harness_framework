"""
Consul HTTP 客户端（框架进程使用）

与 stage-bridge skill 内的 _consul.py 功能等价，但作为模块以便框架进程长期持有。
仅依赖标准库，零外部依赖。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Optional


class ConsulClient:
    def __init__(self, addr: Optional[str] = None, token: Optional[str] = None,
                 timeout: int = 30):
        addr = addr or os.environ.get("CONSUL_ADDR", "127.0.0.1:8500")
        if not addr.startswith(("http://", "https://")):
            addr = "http://" + addr
        self.base = addr.rstrip("/") + "/v1"
        self.token = token or os.environ.get("CONSUL_TOKEN", "")
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["X-Consul-Token"] = self.token
        return h

    def _request(self, method: str, path: str,
                 params: Optional[dict] = None, body: Any = None,
                 timeout: Optional[int] = None) -> tuple[int, bytes, dict]:
        url = self.base + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        data = None
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                data = body.encode("utf-8")
            else:
                data = body

        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers or {})

    # ── KV ──────────────────────────────────────────────────────────────────
    def kv_get(self, key: str, recurse: bool = False
               ) -> tuple[Optional[Any], int]:
        params = {"recurse": "true"} if recurse else None
        code, body, _ = self._request("GET", f"/kv/{key}", params=params)
        if code == 404:
            return None, 0
        if code != 200:
            raise RuntimeError(f"KV GET {key} failed: HTTP {code}")
        items = json.loads(body)
        if not items:
            return None, 0
        if recurse:
            for it in items:
                v = it.get("Value")
                it["_decoded"] = base64.b64decode(v).decode("utf-8") if v else ""
            return items, items[0].get("ModifyIndex", 0)
        item = items[0]
        v = item.get("Value")
        decoded = base64.b64decode(v).decode("utf-8") if v else ""
        return decoded, item.get("ModifyIndex", 0)

    def kv_put(self, key: str, value: str, cas: Optional[int] = None) -> bool:
        params = {"cas": cas} if cas is not None else None
        code, body, _ = self._request("PUT", f"/kv/{key}", params=params, body=value)
        if code != 200:
            raise RuntimeError(f"KV PUT {key} failed: HTTP {code}")
        return body.strip() == b"true"

    def kv_delete(self, key: str, recurse: bool = False) -> None:
        params = {"recurse": "true"} if recurse else None
        code, body, _ = self._request("DELETE", f"/kv/{key}", params=params)
        if code not in (200, 404):
            raise RuntimeError(f"KV DELETE {key} failed: HTTP {code}")

    def kv_blocking_get(self, key: str, index: int = 0,
                        wait: str = "30s", recurse: bool = False
                        ) -> tuple[Optional[Any], int]:
        params = {"index": index, "wait": wait}
        if recurse:
            params["recurse"] = "true"
        code, body, headers = self._request("GET", f"/kv/{key}",
                                            params=params, timeout=60)
        new_index = int(headers.get("X-Consul-Index", index))
        if code == 404:
            return None, new_index
        if code != 200:
            return None, new_index
        items = json.loads(body)
        if not items:
            return None, new_index
        if recurse:
            for it in items:
                v = it.get("Value")
                it["_decoded"] = base64.b64decode(v).decode("utf-8") if v else ""
            return items, new_index
        v = items[0].get("Value")
        decoded = base64.b64decode(v).decode("utf-8") if v else ""
        return decoded, new_index

    # ── Health / Catalog ────────────────────────────────────────────────────
    def list_services(self, service_name: str = "agent-worker") -> list[dict]:
        code, body, _ = self._request("GET", f"/health/service/{service_name}")
        if code != 200:
            return []
        return json.loads(body)

    def service_register(self, payload: dict) -> None:
        code, body, _ = self._request("PUT", "/agent/service/register", body=payload)
        if code != 200:
            raise RuntimeError(f"service register failed: HTTP {code} {body[:200]}")
