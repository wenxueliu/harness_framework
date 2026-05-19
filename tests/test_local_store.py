"""
LocalStore 和 LocalConsulHandler 单元测试
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
import urllib.error

import pytest

from harness_framework.local_store import (
    LocalStore,
    LocalConsulHandler,
    start_local_consul_server,
    _parse_wait,
)


# ── _parse_wait ─────────────────────────────────────────────────────────────

class TestParseWait:
    def test_seconds(self):
        assert _parse_wait("30s") == 30.0

    def test_minutes(self):
        assert _parse_wait("1m") == 60.0

    def test_hours(self):
        assert _parse_wait("1h") == 3600.0

    def test_milliseconds(self):
        assert _parse_wait("500ms") == 0.5

    def test_raw_number(self):
        assert _parse_wait("45") == 45.0

    def test_invalid_defaults_to_30(self):
        assert _parse_wait("abc") == 30.0


# ── LocalStore 核心 KV 操作 ──────────────────────────────────────────────────

class TestLocalStoreKV:
    @pytest.fixture
    def store(self):
        return LocalStore()

    def test_kv_get_single_exists(self, store):
        store.kv_put("test/key", "hello")
        val, idx = store.kv_get("test/key")
        assert val == "hello"
        assert idx > 0

    def test_kv_get_single_not_found(self, store):
        val, idx = store.kv_get("nonexistent")
        assert val is None

    def test_kv_get_recurse(self, store):
        store.kv_put("workflows/req-001/tasks/a/status", "DONE")
        store.kv_put("workflows/req-001/tasks/b/status", "PENDING")
        store.kv_put("workflows/other/key", "value")

        items, idx = store.kv_get("workflows/req-001/", recurse=True)
        assert len(items) == 2
        keys = {it["Key"] for it in items}
        assert "workflows/req-001/tasks/a/status" in keys
        assert "workflows/req-001/tasks/b/status" in keys
        # 验证 _decoded 字段存在
        for it in items:
            assert "_decoded" in it
            assert "Value" in it
            assert "ModifyIndex" in it

    def test_kv_get_recurse_empty(self, store):
        val, idx = store.kv_get("nonexistent/", recurse=True)
        assert val is None

    def test_kv_put_success(self, store):
        ok = store.kv_put("test/key", "value")
        assert ok is True
        v, _ = store.kv_get("test/key")
        assert v == "value"

    def test_kv_put_cas_success(self, store):
        store.kv_put("test/cas", "v1")
        _, idx = store.kv_get("test/cas")
        ok = store.kv_put("test/cas", "v2", cas=idx)
        assert ok is True
        v, _ = store.kv_get("test/cas")
        assert v == "v2"

    def test_kv_put_cas_failure(self, store):
        store.kv_put("test/cas", "v1")
        _, idx = store.kv_get("test/cas")
        # 用错误的 index 做 CAS
        ok = store.kv_put("test/cas", "v2", cas=idx + 999)
        assert ok is False
        v, _ = store.kv_get("test/cas")
        assert v == "v1"  # 值未被覆盖

    def test_kv_put_cas_new_key(self, store):
        """对不存在的 key 做 CAS，current_idx=0。"""
        ok = store.kv_put("new/key", "val", cas=0)
        assert ok is True

        ok = store.kv_put("new/key2", "val", cas=999)
        assert ok is False

    def test_kv_delete(self, store):
        store.kv_put("test/del", "val")
        store.kv_delete("test/del")
        v, _ = store.kv_get("test/del")
        assert v is None

    def test_kv_delete_recurse(self, store):
        store.kv_put("prefix/a", "1")
        store.kv_put("prefix/b", "2")
        store.kv_put("other/c", "3")

        store.kv_delete("prefix/", recurse=True)

        assert store.kv_get("prefix/a")[0] is None
        assert store.kv_get("prefix/b")[0] is None
        assert store.kv_get("other/c")[0] == "3"

    def test_modify_index_monotonic(self, store):
        """验证 modify_index 严格递增。"""
        last_idx = 0
        for i in range(10):
            store.kv_put(f"key{i}", f"val{i}")
            _, idx = store.kv_get(f"key{i}")
            assert idx > last_idx
            last_idx = idx


class TestLocalStoreBlockingGet:
    @pytest.fixture
    def store(self):
        return LocalStore()

    def test_blocking_get_returns_immediately_when_data_exists(self, store):
        store.kv_put("test/bg", "value")
        start = time.time()
        v, idx = store.kv_blocking_get("test/bg", index=0, wait="5s")
        elapsed = time.time() - start
        assert v == "value"
        assert idx > 0
        assert elapsed < 2  # 应该立即返回

    def test_blocking_get_returns_when_changed(self, store):
        store.kv_put("test/bg", "v1")
        _, idx = store.kv_get("test/bg")

        result = []
        err = []

        def waiter():
            try:
                v, _ = store.kv_blocking_get("test/bg", index=idx, wait="10s")
                result.append(v)
            except Exception as e:
                err.append(e)

        t = threading.Thread(target=waiter)
        t.start()

        time.sleep(0.3)  # 让 waiter 先进入等待
        store.kv_put("test/bg", "v2")  # 修改触发唤醒

        t.join(timeout=5)
        assert not t.is_alive()
        assert len(result) == 1
        assert result[0] == "v2"

    def test_blocking_get_times_out(self, store):
        store.kv_put("test/bg", "v1")
        _, idx = store.kv_get("test/bg")

        start = time.time()
        v, new_idx = store.kv_blocking_get("test/bg", index=idx, wait="2s")
        elapsed = time.time() - start

        assert v is None
        assert elapsed >= 2


# ── Agent 心跳和服务注册 ────────────────────────────────────────────────────

class TestLocalStoreServices:
    @pytest.fixture
    def store(self):
        return LocalStore(heartbeat_timeout=60)

    def test_list_services_empty(self, store):
        assert store.list_services() == []

    def test_list_services_healthy(self, store):
        store.register_service({
            "ID": "agent-1",
            "Name": "agent-worker",
            "Tags": ["capability=backend"],
            "Meta": {"service_name": "test-svc"},
        })
        store.record_heartbeat("agent-1")

        services = store.list_services()
        assert len(services) == 1
        assert services[0]["Service"]["ID"] == "agent-1"
        assert services[0]["Checks"][0]["Status"] == "passing"

    def test_list_services_excludes_dead_agents(self, store):
        store.register_service({
            "ID": "agent-dead",
            "Name": "agent-worker",
            "Tags": [],
            "Meta": {},
        })
        # 不发送心跳

        services = store.list_services()
        assert len(services) == 0  # 默认心跳时间为 0，视为死亡

    def test_list_services_filters_by_name(self, store):
        store.register_service({
            "ID": "agent-a",
            "Name": "agent-worker",
            "Tags": [],
            "Meta": {},
        })
        store.register_service({
            "ID": "agent-b",
            "Name": "other-service",
            "Tags": [],
            "Meta": {},
        })
        store.record_heartbeat("agent-a")
        store.record_heartbeat("agent-b")

        services = store.list_services("agent-worker")
        assert len(services) == 1
        assert services[0]["Service"]["ID"] == "agent-a"

    def test_register_and_deregister(self, store):
        store.register_service({
            "ID": "agent-1",
            "Name": "agent-worker",
            "Tags": [],
            "Meta": {},
        })
        store.record_heartbeat("agent-1")
        assert len(store.list_services()) == 1

        store.deregister_service("agent-1")
        assert len(store.list_services()) == 0

    def test_heartbeat_keeps_agent_alive(self, store):
        """心跳更新应让 agent 存活。"""
        store = LocalStore(heartbeat_timeout=2)
        store.register_service({
            "ID": "agent-1",
            "Name": "agent-worker",
            "Tags": [],
            "Meta": {},
        })
        store.record_heartbeat("agent-1")
        assert len(store.list_services()) == 1

        time.sleep(2.5)
        assert len(store.list_services()) == 0  # 心跳超时

        store.record_heartbeat("agent-1")
        assert len(store.list_services()) == 1  # 重新激活


# ── 持久化 ──────────────────────────────────────────────────────────────────

class TestLocalStorePersistence:
    def test_save_and_restore(self, tmp_path):
        data_file = str(tmp_path / "store.json")

        store1 = LocalStore(data_file=data_file)
        store1.kv_put("key1", "val1")
        store1.kv_put("key2", "val2")
        store1.register_service({
            "ID": "agent-1",
            "Name": "agent-worker",
            "Tags": [],
            "Meta": {},
        })
        store1.record_heartbeat("agent-1")
        store1.flush()

        # 创建新 store 从文件加载
        store2 = LocalStore(data_file=data_file)
        v1, _ = store2.kv_get("key1")
        v2, _ = store2.kv_get("key2")
        assert v1 == "val1"
        assert v2 == "val2"

        services = store2.list_services()
        assert len(services) == 1
        assert services[0]["Service"]["ID"] == "agent-1"

    def test_no_data_file_no_persistence(self, tmp_path):
        """不提供 data_file 时不应创建任何文件。"""
        store = LocalStore()
        store.kv_put("key1", "val1")
        # flush 不应报错
        store.flush()


# ── 线程安全 ────────────────────────────────────────────────────────────────

class TestLocalStoreThreadSafety:
    def test_concurrent_kv_operations(self):
        store = LocalStore()
        errors = []
        N = 50

        def writer(prefix):
            for i in range(N):
                try:
                    store.kv_put(f"{prefix}/{i}", f"val-{i}")
                except Exception as e:
                    errors.append(e)

        t1 = threading.Thread(target=writer, args=("a",))
        t2 = threading.Thread(target=writer, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0
        # 验证所有 key 都被写入
        for prefix in ("a", "b"):
            for i in range(N):
                v, _ = store.kv_get(f"{prefix}/{i}")
                assert v == f"val-{i}"

    def test_concurrent_cas(self):
        """两个线程同时 CAS 同一 key，只有一个成功。"""
        store = LocalStore()
        store.kv_put("race/key", "init")
        _, idx = store.kv_get("race/key")

        results = []

        def cas_worker(wanted_value):
            ok = store.kv_put("race/key", wanted_value, cas=idx)
            results.append(ok)

        t1 = threading.Thread(target=cas_worker, args=("t1",))
        t2 = threading.Thread(target=cas_worker, args=("t2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # 只有一个成功
        assert sum(results) == 1
        v, _ = store.kv_get("race/key")
        # 值应是成功者的值
        assert v in ("t1", "t2")


# ── LocalConsulHandler HTTP 集成测试 ────────────────────────────────────────

class TestLocalConsulHTTP:
    @pytest.fixture
    def http_store(self):
        """启动嵌入式 HTTP 服务器，返回 (store, port)。"""
        store = LocalStore()
        server, thread = start_local_consul_server(
            store, host="127.0.0.1", port=0)
        port = server.server_address[1]
        yield store, port
        server.shutdown()
        thread.join(timeout=2)

    def _url(self, port, path, params=None):
        u = f"http://127.0.0.1:{port}{path}"
        if params:
            from urllib.parse import urlencode
            u += "?" + urlencode(params)
        return u

    # ── KV GET ───────────────────────────────────────────────────────────

    def test_kv_get_via_http(self, http_store):
        store, port = http_store
        store.kv_put("test/http", "hello")

        url = self._url(port, "/v1/kv/test/http")
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200
            data = json.loads(resp.read())
        assert len(data) == 1
        assert data[0]["Key"] == "test/http"
        # Value 应为 base64
        import base64
        decoded = base64.b64decode(data[0]["Value"]).decode("utf-8")
        assert decoded == "hello"

    def test_kv_get_not_found(self, http_store):
        _, port = http_store
        url = self._url(port, "/v1/kv/nonexistent")
        try:
            urllib.request.urlopen(url)
            assert False, "expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404

    def test_kv_get_recurse_via_http(self, http_store):
        store, port = http_store
        store.kv_put("prefix/a", "1")
        store.kv_put("prefix/b", "2")

        url = self._url(port, "/v1/kv/prefix/", params={"recurse": "true"})
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200
            data = json.loads(resp.read())
        assert len(data) == 2

    # ── KV PUT ───────────────────────────────────────────────────────────

    def test_kv_put_via_http(self, http_store):
        store, port = http_store
        url = self._url(port, "/v1/kv/test/put")
        data = b"test-value"
        req = urllib.request.Request(url, data=data, method="PUT")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert resp.read() == b"true"

        v, _ = store.kv_get("test/put")
        assert v == "test-value"

    def test_kv_put_cas_via_http(self, http_store):
        store, port = http_store
        store.kv_put("test/cas", "v1")
        _, idx = store.kv_get("test/cas")

        # CAS 成功
        url = self._url(port, "/v1/kv/test/cas", params={"cas": idx})
        req = urllib.request.Request(url, data=b"v2", method="PUT")
        with urllib.request.urlopen(req) as resp:
            assert resp.read() == b"true"

        # CAS 失败
        url = self._url(port, "/v1/kv/test/cas", params={"cas": idx})  # 旧 index
        req = urllib.request.Request(url, data=b"v3", method="PUT")
        with urllib.request.urlopen(req) as resp:
            assert resp.read() == b"false"

        v, _ = store.kv_get("test/cas")
        assert v == "v2"  # v3 被拒绝

    # ── KV DELETE ────────────────────────────────────────────────────────

    def test_kv_delete_via_http(self, http_store):
        store, port = http_store
        store.kv_put("test/del", "val")

        url = self._url(port, "/v1/kv/test/del")
        req = urllib.request.Request(url, method="DELETE")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        assert store.kv_get("test/del")[0] is None

    def test_kv_delete_recurse_via_http(self, http_store):
        store, port = http_store
        store.kv_put("prefix/a", "1")
        store.kv_put("prefix/b", "2")

        url = self._url(port, "/v1/kv/prefix/", params={"recurse": "true"})
        req = urllib.request.Request(url, method="DELETE")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        assert store.kv_get("prefix/a")[0] is None
        assert store.kv_get("prefix/b")[0] is None

    # ── Agent Service ────────────────────────────────────────────────────

    def test_service_register_via_http(self, http_store):
        store, port = http_store
        payload = json.dumps({
            "ID": "agent-http",
            "Name": "agent-worker",
            "Tags": ["capability=backend"],
            "Meta": {"service_name": "test"},
        }).encode("utf-8")

        url = self._url(port, "/v1/agent/service/register")
        req = urllib.request.Request(url, data=payload, method="PUT",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        # 注册后需要心跳才能出现在 list_services 中
        store.record_heartbeat("agent-http")
        services = store.list_services()
        assert len(services) == 1
        assert services[0]["Service"]["ID"] == "agent-http"

    def test_service_deregister_via_http(self, http_store):
        store, port = http_store
        store.register_service({
            "ID": "agent-del",
            "Name": "agent-worker",
            "Tags": [],
            "Meta": {},
        })

        url = self._url(port, "/v1/agent/service/deregister/agent-del")
        req = urllib.request.Request(url, method="PUT")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        assert len(store.list_services()) == 0

    # ── Health Check / Heartbeat ─────────────────────────────────────────

    def test_check_pass_via_http(self, http_store):
        store, port = http_store
        store.register_service({
            "ID": "agent-hb",
            "Name": "agent-worker",
            "Tags": [],
            "Meta": {},
        })

        # 服务未心跳，应算死亡
        assert len(store.list_services()) == 0

        url = self._url(port, "/v1/agent/check/pass/service:agent-hb",
                        params={"note": "alive"})
        req = urllib.request.Request(url, method="PUT")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200

        assert len(store.list_services()) == 1

    # ── Health Service ───────────────────────────────────────────────────

    def test_health_service_via_http(self, http_store):
        store, port = http_store
        store.register_service({
            "ID": "agent-hs",
            "Name": "agent-worker",
            "Tags": ["env=test"],
            "Meta": {},
        })
        store.record_heartbeat("agent-hs")

        url = self._url(port, "/v1/health/service/agent-worker")
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200
            data = json.loads(resp.read())
        assert len(data) == 1
        assert data[0]["Service"]["ID"] == "agent-hs"
        assert data[0]["Checks"][0]["Status"] == "passing"

    # ── Status Leader ────────────────────────────────────────────────────

    def test_status_leader_via_http(self, http_store):
        _, port = http_store
        url = self._url(port, "/v1/status/leader")
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200

    # ── Agent Self ───────────────────────────────────────────────────────

    def test_agent_self_via_http(self, http_store):
        _, port = http_store
        url = self._url(port, "/v1/agent/self")
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200


# ── KVStore Protocol 兼容性 ─────────────────────────────────────────────────

class TestKVStoreProtocol:
    """验证 ConsulClient 和 LocalStore 都满足 KVStore Protocol。"""
    def test_local_store_satisfies_protocol(self):
        from harness_framework.kv_store_protocol import KVStore
        store = LocalStore()
        # 结构性子类型检查
        assert isinstance(store, KVStore)

    def test_consul_client_satisfies_protocol(self):
        from harness_framework.kv_store_protocol import KVStore
        from harness_framework.consul_client import ConsulClient
        client = ConsulClient.__new__(ConsulClient)
        # ConsulClient 实例结构上满足 KVStore
        assert isinstance(client, KVStore)
