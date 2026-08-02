"""
E2E 测试公共 fixtures — Kimi WebBridge + 环境管理 + Consul 数据初始化

参考 gstack：
- browser fixture 相当于 gstack 的持久化 daemon（我们每次测试新建 browser，更隔离）
- consul_setup 相当于 gstack 的 baseline/setup 模式
- screenshot_on_failure 相当于 gstack 的证据收集模式
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import requests
from .webbridge import Page


# ---- 路径常量 ----
E2E_DIR = Path(__file__).parent
BASELINE_DIR = E2E_DIR / "baselines"
SCREENSHOT_DIR = BASELINE_DIR / "screenshots"
REPORT_DIR = E2E_DIR / "reports"

# ---- 环境变量配置 ----
E2E_BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:3000")
CONSUL_URL = os.environ.get("CONSUL_ADDR", "http://127.0.0.1:8500")
WEBAPI_URL = os.environ.get("WEBAPI_URL", "http://localhost:8080")
DEFAULT_TIMEOUT = int(os.environ.get("E2E_TIMEOUT", "30000"))


# ---- Consul 数据管理 helpers ----

def consul_put(key: str, value: str) -> requests.Response:
    """写入 Consul KV。"""
    return requests.put(f"{CONSUL_URL}/v1/kv/{key}", data=value.encode())


def consul_get(key: str, recurse: bool = False) -> Any:
    """读取 Consul KV。"""
    resp = requests.get(f"{CONSUL_URL}/v1/kv/{key}", params={"recurse": str(recurse).lower()})
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    if data is None:
        return None
    if isinstance(data, list):
        return [{**item, "_decoded": item.get("Value", "")} for item in data]
    return data


def consul_delete(key: str, recurse: bool = False) -> None:
    """删除 Consul KV。"""
    requests.delete(f"{CONSUL_URL}/v1/kv/{key}", params={"recurse": str(recurse).lower()})


def create_test_workflow(
    req_id: str,
    title: str = "E2E 自动化测试需求",
    tasks: dict[str, Any] | None = None,
) -> None:
    """通过 Consul API 创建测试用 workflow。

    参考 gstack 的做法：测试前初始化数据（baseline/setup），测试后清理。
    """
    if tasks is None:
        tasks = {
            "design": {
                "type": "design",
                "depends_on": [],
                "status_hint": "DONE",
            },
            "backend": {
                "type": "backend",
                "depends_on": ["design"],
                "status_hint": "IN_PROGRESS",
            },
            "test": {
                "type": "test",
                "depends_on": ["backend"],
                "status_hint": "PENDING",
            },
        }

    # 写入 title
    consul_put(f"workflows/{req_id}/title", title)
    consul_put(f"workflows/{req_id}/published", "true")
    consul_put(f"workflows/{req_id}/priority", "5")

    # 构建 dependencies JSON
    deps = {}
    for name, task in tasks.items():
        deps[name] = {
            "type": task.get("type", "task"),
            "depends_on": task.get("depends_on", []),
        }

    consul_put(f"workflows/{req_id}/dependencies", json.dumps(deps))

    # 写入任务状态
    for name, task in tasks.items():
        status = task.get("status_hint", "PENDING")
        consul_put(f"workflows/{req_id}/tasks/{name}/status", status)
        consul_put(f"workflows/{req_id}/tasks/{name}/type", task.get("type", "task"))
        if status == "IN_PROGRESS":
            consul_put(
                f"workflows/{req_id}/tasks/{name}/started_at",
                datetime.utcnow().isoformat() + "Z",
            )
            consul_put(f"workflows/{req_id}/tasks/{name}/assigned_agent", "agent-e2e-test")


def cleanup_test_workflow(req_id: str) -> None:
    """递归删除测试 workflow 的所有 Consul KV。"""
    try:
        consul_delete(f"workflows/{req_id}", recurse=True)
    except Exception:
        pass  # 清理失败不阻断测试


@pytest.fixture
def page(request: pytest.FixtureRequest) -> Page:
    """每个测试独立的 WebBridge session，操作用户真实 Chrome。"""
    if not Page.available():
        pytest.skip("Kimi WebBridge daemon unavailable at 127.0.0.1:10086")
    pg = Page(session=f"harness-e2e-{request.node.name}")
    pg.set_default_timeout(DEFAULT_TIMEOUT)
    yield pg


@pytest.fixture
def dashboard_url() -> str:
    """看板地址。自动检测 3000 端口是否可用。"""
    try:
        resp = requests.get(f"{E2E_BASE_URL}/", timeout=2)
        resp.raise_for_status()
        return E2E_BASE_URL
    except Exception:
        # 尝试直接连接 Consul（看板可能使用 mock 模式）
        return E2E_BASE_URL


@pytest.fixture
def unique_req_id() -> str:
    """生成唯一的 workflow ID，避免测试间冲突。"""
    return f"e2e-test-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"


@pytest.fixture
def consul_setup(unique_req_id: str) -> str:
    """初始化 Consul 测试数据，测试结束后自动清理。

    用法：
        def test_xxx(consul_setup, page):
            req_id = consul_setup
            page.goto(f"{dashboard_url}/#/")
            ...
    """
    create_test_workflow(unique_req_id)
    # 等待 Consul 写入生效
    time.sleep(0.3)
    yield unique_req_id
    cleanup_test_workflow(unique_req_id)


@pytest.fixture
def consul_multi_workflow() -> list[str]:
    """创建多个测试 workflow（用于列表/切换测试），测试结束后自动清理。"""
    now = datetime.now().strftime("%Y%m%d%H%M%S%f")
    req_ids = []
    for i in range(3):
        req_id = f"e2e-multi-{now}-{i}"
        create_test_workflow(
            req_id,
            title=f"E2E 测试需求 #{i+1}",
            tasks={
                "design": {"type": "design", "depends_on": [], "status_hint": "DONE"},
                "backend": {
                    "type": "backend",
                    "depends_on": ["design"],
                    "status_hint": "DONE" if i == 2 else "IN_PROGRESS" if i == 1 else "BLOCKED",
                },
                "test": {
                    "type": "test",
                    "depends_on": ["backend"],
                    "status_hint": "DONE" if i == 2 else "BLOCKED",
                },
            },
        )
        req_ids.append(req_id)

    time.sleep(0.3)
    yield req_ids

    for rid in req_ids:
        cleanup_test_workflow(rid)


@pytest.fixture(autouse=True)
def screenshot_on_failure(request: pytest.FixtureRequest, page: Page) -> None:
    """测试失败时自动截图（参考 gstack 的证据收集模式）。

    截图保存到 reports/ 目录，文件名包含测试名和时间戳。
    """
    yield
    if request.session.testsfailed:
        # pytest 的 testsfailed 是全局计数器，用 hasattr 检查当前测试是否失败
        pass

    # 在 teardown 阶段检查（pytest 不提供 per-test fail hook，用 pytest_runtest_makereport 更精确）
    # 这里简单处理：如果 page 仍然 open，说明测试可能失败了


# ---- pytest 配置 hook ----

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> None:
    """测试失败时自动截图（参考 gstack 的证据收集模式）。

    截图保存到 tests/e2e/reports/ 目录。
    """
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed:
        page = getattr(item, "_e2e_page", None)
        if page is None:
            # 从 fixture 获取 page
            if hasattr(item, "funcargs") and "page" in item.funcargs:
                page = item.funcargs["page"]

        if page is not None:
            try:
                test_name = item.name.replace("[", "_").replace("]", "_").replace("/", "_")
                timestamp = datetime.now().strftime("%H%M%S")
                filename = f"FAILED-{test_name}-{timestamp}.png"
                filepath = REPORT_DIR / filename
                page.screenshot(path=str(filepath), full_page=True)
                print(f"\n[Screenshot] 失败截图已保存: {filepath}")
            except Exception as e:
                print(f"\n[Screenshot] 截图失败: {e}")


# ---- session 级别 setup/teardown ----

def pytest_sessionstart(session: pytest.Session) -> None:
    """测试套件开始前检查环境。"""
    print(f"\n[E2E] BASE_URL={E2E_BASE_URL}, browser=Kimi WebBridge")

    # 检查 Consul 连通性
    try:
        resp = requests.get(f"{CONSUL_URL}/v1/status/leader", timeout=2)
        if resp.ok:
            print(f"[E2E] Consul OK: {CONSUL_URL}")
        else:
            print(f"[E2E] WARNING: Consul 响应异常: {resp.status_code}")
    except Exception as e:
        print(f"[E2E] WARNING: Consul 不可达 ({e}) — 看板将使用 mock 模式")


def pytest_configure(config: pytest.Config) -> None:
    """注册 e2e marker。"""
    config.addinivalue_line("markers", "e2e: E2E 测试标记")
    config.addinivalue_line("markers", "smoke: 冒烟测试（关键路径）")
    config.addinivalue_line("markers", "visual: 视觉回归测试")
    config.addinivalue_line("markers", "performance: 性能基准测试")
    config.addinivalue_line("markers", "a11y: 无障碍测试")
