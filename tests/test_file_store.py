"""
FileStore 单元测试 — 基于 JSON 文件的纯本地 KV 存储
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from harness_framework.file_store import FileStore, DEFAULT_DATA_FILE, _parse_wait


# ── Helpers ─────────────────────────────────────────────────────────────────

def _read_raw_file(path):
    with open(path, "r") as f:
        return json.load(f)


@pytest.fixture
def store(tmp_path):
    """创建使用临时文件的 FileStore。"""
    data_file = str(tmp_path / "test_store.json")
    return FileStore(data_file=data_file)


@pytest.fixture
def store_hb(tmp_path):
    """带短心跳超时的 FileStore。"""
    data_file = str(tmp_path / "test_hb.json")
    return FileStore(data_file=data_file, heartbeat_timeout=2)


# ── KV 操作 ─────────────────────────────────────────────────────────────────

class TestFileStoreKV:
    def test_kv_get_single_exists(self, store):
        store.kv_put("test/key", "hello")
        v, idx = store.kv_get("test/key")
        assert v == "hello"
        assert idx > 0

    def test_kv_get_single_not_found(self, store):
        v, idx = store.kv_get("nonexistent")
        assert v is None

    def test_kv_get_recurse(self, store):
        store.kv_put("workflows/req-001/tasks/a/status", "DONE")
        store.kv_put("workflows/req-001/tasks/b/status", "PENDING")
        store.kv_put("workflows/other/key", "value")

        items, idx = store.kv_get("workflows/req-001/", recurse=True)
        assert len(items) == 2
        keys = {it["Key"] for it in items}
        assert "workflows/req-001/tasks/a/status" in keys
        assert "workflows/req-001/tasks/b/status" in keys
        for it in items:
            assert "_decoded" in it
            assert "Value" in it
            assert "ModifyIndex" in it

    def test_kv_get_recurse_empty(self, store):
        v, idx = store.kv_get("nonexistent/", recurse=True)
        assert v is None

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
        ok = store.kv_put("test/cas", "v2", cas=idx + 999)
        assert ok is False
        v, _ = store.kv_get("test/cas")
        assert v == "v1"

    def test_kv_put_cas_new_key(self, store):
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
        last_idx = 0
        for i in range(10):
            store.kv_put(f"key{i}", f"val{i}")
            _, idx = store.kv_get(f"key{i}")
            assert idx > last_idx
            last_idx = idx

    def test_data_persisted_to_file(self, store):
        store.kv_put("persist/key", "hello")
        raw = _read_raw_file(store.data_file)
        assert "persist/key" in raw["store"]
        assert raw["store"]["persist/key"][0] == "hello"


# ── Blocking Get ────────────────────────────────────────────────────────────

class TestFileStoreBlockingGet:
    def test_blocking_get_returns_immediately(self, store):
        store.kv_put("test/bg", "value")
        start = time.time()
        v, idx = store.kv_blocking_get("test/bg", index=0, wait="5s")
        elapsed = time.time() - start
        assert v == "value"
        assert elapsed < 2

    def test_blocking_get_wakes_on_change(self, store):
        store.kv_put("test/bg", "v1")
        _, idx = store.kv_get("test/bg")

        result = []

        def waiter():
            v, _ = store.kv_blocking_get("test/bg", index=idx, wait="10s")
            result.append(v)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.3)
        store.kv_put("test/bg", "v2")
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


# ── Agent 服务/心跳 ─────────────────────────────────────────────────────────

class TestFileStoreServices:
    def test_list_services_empty(self, store):
        assert store.list_services() == []

    def test_list_services_healthy_after_heartbeat(self, store):
        store.register_service({
            "ID": "agent-1",
            "Name": "agent-worker",
            "Tags": ["capability=backend"],
            "Meta": {},
        })
        assert store.list_services() == []  # 未心跳
        store.record_heartbeat("agent-1")
        services = store.list_services()
        assert len(services) == 1
        assert services[0]["Service"]["ID"] == "agent-1"

    def test_list_services_excludes_dead(self, store_hb):
        store_hb.register_service({
            "ID": "agent-dead",
            "Name": "agent-worker",
            "Tags": [],
            "Meta": {},
        })
        store_hb.record_heartbeat("agent-dead")
        assert len(store_hb.list_services()) == 1
        time.sleep(2.5)
        assert len(store_hb.list_services()) == 0

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

    def test_filters_by_service_name(self, store):
        store.register_service({
            "ID": "a", "Name": "agent-worker", "Tags": [], "Meta": {},
        })
        store.register_service({
            "ID": "b", "Name": "other-svc", "Tags": [], "Meta": {},
        })
        store.record_heartbeat("a")
        store.record_heartbeat("b")
        assert len(store.list_services("agent-worker")) == 1


# ── 进程间并发 ──────────────────────────────────────────────────────────────

class TestFileStoreProcessConcurrency:
    def test_two_stores_share_data(self, tmp_path):
        """两个 FileStore 实例指向同一文件，应共享数据。"""
        data_file = str(tmp_path / "shared.json")
        s1 = FileStore(data_file=data_file)
        s2 = FileStore(data_file=data_file)

        s1.kv_put("shared/key", "from-s1")
        v, idx = s2.kv_get("shared/key")
        assert v == "from-s1"

        s2.kv_put("shared/key", "from-s2")
        v, idx = s1.kv_get("shared/key")
        assert v == "from-s2"

    def test_cas_across_instances(self, tmp_path):
        """CAS 在跨实例时仍然正确。"""
        data_file = str(tmp_path / "cas.json")
        s1 = FileStore(data_file=data_file)
        s2 = FileStore(data_file=data_file)

        s1.kv_put("cas/key", "v1")
        _, idx = s1.kv_get("cas/key")

        ok = s1.kv_put("cas/key", "v1-from-s1", cas=idx)
        assert ok is True

        ok = s2.kv_put("cas/key", "v2-from-s2", cas=idx)  # 过期 index
        assert ok is False

        v, _ = s1.kv_get("cas/key")
        assert v == "v1-from-s1"


# ── CLI 命令测试 ────────────────────────────────────────────────────────────

class TestFileStoreCLI:
    def _run_cli(self, tmp_path, *args):
        """运行 file_kv.py CLI 并返回 (returncode, stdout, stderr)。"""
        data_file = str(tmp_path / "cli_store.json")
        script = os.path.join(
            os.path.dirname(__file__), "..", "scripts", "file_kv.py")
        full_args = [sys.executable, script, "--data-file", data_file] + list(args)
        r = subprocess.run(full_args, capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def test_put_and_get(self, tmp_path):
        rc, out, _ = self._run_cli(tmp_path, "put", "cli/test", "hello")
        assert rc == 0
        assert out == "true"

        rc, out, _ = self._run_cli(tmp_path, "get", "cli/test")
        assert rc == 0
        data = json.loads(out)
        assert data[0]["Key"] == "cli/test"
        import base64
        decoded = base64.b64decode(data[0]["Value"]).decode("utf-8")
        assert decoded == "hello"

    def test_get_not_found(self, tmp_path):
        rc, out, _ = self._run_cli(tmp_path, "get", "no/such/key")
        assert rc == 1  # exit 1 for not found

    def test_put_with_cas(self, tmp_path):
        self._run_cli(tmp_path, "put", "cli/cas", "v1")
        # Get index
        rc, out, _ = self._run_cli(tmp_path, "get", "cli/cas")
        idx = json.loads(out)[0]["ModifyIndex"]

        # CAS with correct index
        rc, out, _ = self._run_cli(tmp_path, "put", "cli/cas", "v2", "--cas", str(idx))
        assert rc == 0
        assert out == "true"

        # CAS with wrong index
        rc, out, _ = self._run_cli(tmp_path, "put", "cli/cas", "v3", "--cas", "1")
        assert rc == 1
        assert out == "false"

    def test_delete(self, tmp_path):
        self._run_cli(tmp_path, "put", "cli/del", "val")
        rc, out, _ = self._run_cli(tmp_path, "delete", "cli/del")
        assert rc == 0

        rc, out, _ = self._run_cli(tmp_path, "get", "cli/del")
        assert rc == 1

    def test_delete_recurse(self, tmp_path):
        self._run_cli(tmp_path, "put", "prefix/a", "1")
        self._run_cli(tmp_path, "put", "prefix/b", "2")
        rc, out, _ = self._run_cli(tmp_path, "delete", "prefix/", "--recurse")
        assert rc == 0

        rc, out, _ = self._run_cli(tmp_path, "get", "prefix/a")
        assert rc == 1

    def test_register_heartbeat_list(self, tmp_path):
        payload = json.dumps({
            "ID": "agent-cli",
            "Name": "agent-worker",
            "Tags": ["test"],
            "Meta": {},
        })
        self._run_cli(tmp_path, "register", payload)
        self._run_cli(tmp_path, "heartbeat", "agent-cli")

        rc, out, _ = self._run_cli(tmp_path, "list-services")
        assert rc == 0
        services = json.loads(out)
        assert len(services) == 1
        assert services[0]["Service"]["ID"] == "agent-cli"

    def test_deregister(self, tmp_path):
        payload = json.dumps({
            "ID": "agent-cli",
            "Name": "agent-worker",
            "Tags": [], "Meta": {},
        })
        self._run_cli(tmp_path, "register", payload)
        self._run_cli(tmp_path, "deregister", "agent-cli")

        # 即使心跳过，deregister 后也不应出现
        self._run_cli(tmp_path, "heartbeat", "agent-cli")
        rc, out, _ = self._run_cli(tmp_path, "list-services")
        services = json.loads(out)
        assert len(services) == 0

    def test_status_leader(self, tmp_path):
        rc, out, _ = self._run_cli(tmp_path, "status-leader")
        assert rc == 0
        assert "127.0.0.1" in out

    def test_blocking_get(self, tmp_path):
        # Start blocking get in background, then write
        self._run_cli(tmp_path, "put", "bg/key", "v1")
        rc, out, _ = self._run_cli(tmp_path, "get", "bg/key")
        assert rc == 0

        # blocking-get with index=0 should return immediately since data exists
        rc, out, _ = self._run_cli(
            tmp_path, "blocking-get", "bg/key", "--index", "0", "--wait", "2s")
        assert rc == 0
        data = json.loads(out)
        assert data[0]["Key"] == "bg/key"

    def test_get_recurse(self, tmp_path):
        self._run_cli(tmp_path, "put", "ns/a", "1")
        self._run_cli(tmp_path, "put", "ns/b", "2")
        rc, out, _ = self._run_cli(tmp_path, "get", "ns/", "--recurse")
        assert rc == 0
        data = json.loads(out)
        assert len(data) == 2


# ── Protocol 兼容性 ─────────────────────────────────────────────────────────

class TestFileStoreProtocol:
    def test_file_store_satisfies_protocol(self):
        from harness_framework.kv_store_protocol import KVStore
        store = FileStore.__new__(FileStore)
        assert isinstance(store, KVStore)
