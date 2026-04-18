"""
_consul.py — stage-bridge 共享 Consul HTTP 客户端
所有命令脚本都通过本模块访问 Consul。
环境变量：
  CONSUL_ADDR    Consul HTTP 地址，默认 127.0.0.1:8500
  CONSUL_TOKEN   ACL Token（dev mode 可省略）
  AGENT_ID       当前 Agent 的全局唯一 ID
  REQ_ID         当前需求 ID（命令调用时也可显式传参覆盖）
  TASK_NAME      当前任务名称
  SERVICE_NAME   绑定的微服务名称（开发 Agent 必填）
  REPO_PATH      微服务代码仓库本地路径
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Optional


# ── 环境读取 ──────────────────────────────────────────────────────────────────

def env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    v = os.environ.get(name, default)
    if required and not v:
        die(f"环境变量 {name} 未设置", code=2)
    return v or ""


def consul_base_url() -> str:
    addr = env("CONSUL_ADDR", "127.0.0.1:8500")
    if not addr.startswith(("http://", "https://")):
        addr = "http://" + addr
    return addr.rstrip("/") + "/v1"


def consul_headers() -> dict:
    h = {"Content-Type": "application/json"}
    token = env("CONSUL_TOKEN", "")
    if token:
        h["X-Consul-Token"] = token
    return h


# ── HTTP 调用 ─────────────────────────────────────────────────────────────────

def http_request(
    method: str,
    path: str,
    params: Optional[dict] = None,
    body: Any = None,
    timeout: int = 30,
) -> tuple[int, bytes, dict]:
    """返回 (status_code, body_bytes, response_headers)"""
    url = consul_base_url() + path
    if params:
        # 过滤掉 None 值
        clean_params = {k: v for k, v in params.items() if v is not None}
        if clean_params:
            url += "?" + urllib.parse.urlencode(clean_params)

    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body

    req = urllib.request.Request(url, data=data, method=method, headers=consul_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except Exception as e:
        die(f"Consul 请求失败 {method} {path}: {e}", code=2)


# ── KV 操作 ───────────────────────────────────────────────────────────────────

def kv_get(key: str, recurse: bool = False) -> tuple[Optional[Any], int]:
    """
    返回 (value_or_list, modify_index)
    - 若 recurse=False 返回单个值（字符串）或 None
    - 若 recurse=True 返回 [{Key, Value, ModifyIndex}, ...] 或 None
    """
    params = {"recurse": "true"} if recurse else None
    code, body, headers = http_request("GET", f"/kv/{key}", params=params)
    if code == 404:
        return None, 0
    if code != 200:
        die(f"KV GET 失败 {key}: HTTP {code} {body[:200]}", code=2)

    items = json.loads(body)
    if not items:
        return None, 0

    if recurse:
        decoded = []
        for it in items:
            v = it.get("Value")
            it["_decoded"] = base64.b64decode(v).decode("utf-8") if v else ""
            decoded.append(it)
        return decoded, items[0].get("ModifyIndex", 0)
    else:
        item = items[0]
        v = item.get("Value")
        decoded = base64.b64decode(v).decode("utf-8") if v else ""
        return decoded, item.get("ModifyIndex", 0)


def kv_put(key: str, value: str, cas: Optional[int] = None) -> bool:
    """写入 KV。若指定 cas（ModifyIndex），仅在未变更时写入成功（CAS 原子操作）。"""
    params = {"cas": cas} if cas is not None else None
    code, body, _ = http_request("PUT", f"/kv/{key}", params=params, body=value)
    if code != 200:
        die(f"KV PUT 失败 {key}: HTTP {code} {body[:200]}", code=2)
    return body.strip() == b"true"


def kv_delete(key: str, recurse: bool = False) -> None:
    params = {"recurse": "true"} if recurse else None
    code, body, _ = http_request("DELETE", f"/kv/{key}", params=params)
    if code not in (200, 404):
        die(f"KV DELETE 失败 {key}: HTTP {code} {body[:200]}", code=2)


def kv_blocking_get(
    key: str, index: int = 0, wait: str = "30s", recurse: bool = False
) -> tuple[Optional[Any], int]:
    """阻塞查询：在 KV 变更或超时（wait）时返回。"""
    params = {"index": index, "wait": wait}
    if recurse:
        params["recurse"] = "true"
    code, body, headers = http_request("GET", f"/kv/{key}", params=params, timeout=60)
    new_index = int(headers.get("X-Consul-Index", index))
    if code == 404:
        return None, new_index
    if code != 200:
        die(f"KV blocking GET 失败 {key}: HTTP {code}", code=2)
    items = json.loads(body)
    if not items:
        return None, new_index
    if recurse:
        for it in items:
            v = it.get("Value")
            it["_decoded"] = base64.b64decode(v).decode("utf-8") if v else ""
        return items, new_index
    else:
        v = items[0].get("Value")
        decoded = base64.b64decode(v).decode("utf-8") if v else ""
        return decoded, new_index


# ── 服务注册 ──────────────────────────────────────────────────────────────────

def service_register(payload: dict) -> None:
    code, body, _ = http_request("PUT", "/agent/service/register", body=payload)
    if code != 200:
        die(f"服务注册失败: HTTP {code} {body[:200]}", code=2)


def service_deregister(service_id: str) -> None:
    code, _, _ = http_request("PUT", f"/agent/service/deregister/{service_id}")
    if code != 200:
        die(f"服务注销失败 {service_id}: HTTP {code}", code=2)


def health_check_pass(check_id: str, note: str = "") -> None:
    params = {"note": note} if note else None
    code, _, _ = http_request("PUT", f"/agent/check/pass/{check_id}", params=params)
    if code != 200 and code != 404:
        # 404 表示 Check 不存在，记日志但不致命
        sys.stderr.write(f"[warn] health check pass 失败 {check_id}: HTTP {code}\n")


# ── 输出辅助 ──────────────────────────────────────────────────────────────────

def emit_json(obj: Any) -> None:
    """统一 JSON 输出到 stdout。"""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def die(msg: str, code: int = 1) -> None:
    """错误退出：消息走 stderr，退出码区分业务错（1）/ 系统错（2）。"""
    sys.stderr.write(f"[stage-bridge:error] {msg}\n")
    sys.exit(code)


def now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


# ── 路径工具 ──────────────────────────────────────────────────────────────────

def task_base(req_id: str, task_name: str) -> str:
    return f"workflows/{req_id}/tasks/{task_name}"


def context_base(req_id: str) -> str:
    return f"workflows/{req_id}/context"


def session_base(req_id: str, task_name: str, session_id: str) -> str:
    return f"workflows/{req_id}/sessions/{task_name}/{session_id}"


def feedback_base(req_id: str, service: str) -> str:
    return f"workflows/{req_id}/feedback/{service}"
