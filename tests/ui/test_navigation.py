"""UI test cases: deterministic back navigation and board header cleanup.

覆盖本轮回归的导航行为：
- 执行日志页 → 看板
- 实例详情 → 所属模板详情
- 模板详情 → 所属项目详情
- 看板左上角品牌 → 项目首页
- 看板头部已移除的按钮（新建项目 / 刷新 / 项目 tabs）不再出现
- 实例创建向导的项目工作区必选校验
"""
from __future__ import annotations

import os
import time
import uuid
from urllib.parse import quote

import pytest
import requests

from tests.e2e.conftest import WEBAPI_URL, consul_delete
from tests.e2e.webbridge import Page

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:3000")


def _api(method: str, path: str, body: dict | None = None) -> dict:
    response = requests.request(
        method, f"{WEBAPI_URL}{path}",
        json=body, timeout=10,
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    response.raise_for_status()
    return response.json()


def _click_button_with_text(page: Page, text: str) -> bool:
    return bool(page.evaluate("""(text) => {
      const btn = [...document.querySelectorAll('button, a')]
        .find(el => el.textContent.trim().includes(text));
      if (!btn) return false;
      btn.click();
      return true;
    }""", text))


def _create_group(page: Page, prefix: str) -> str:
    name = f"{prefix}-{time.time_ns()}"
    group = _api("POST", "/api/project-groups", {"name": name, "description": "ui-nav"})
    return group["project_group"]["group_id"]


def _cleanup_group(group_id: str) -> None:
    try:
        consul_delete(f"project-groups/{quote(group_id, safe='')}", recurse=True)
    except Exception:
        pass


def _create_template(group_id: str, name: str) -> str:
    payload = _api("POST", "/api/templates", {
        "name": name, "description": "ui-nav", "group_id": group_id,
        "tasks": [{"id": "build", "name": "构建", "type": "backend",
                   "agent": "codex", "depends_on": []}],
    })
    return payload["template"]["template_id"]


def _cleanup_template(template_id: str) -> None:
    try:
        consul_delete(f"jobflows/templates/{quote(template_id, safe='')}", recurse=True)
    except Exception:
        pass


def _create_instance(template_id: str, version_id: str, name: str) -> str:
    payload = _api("POST", "/api/instances", {
        "template_id": template_id, "version_id": version_id,
        "name": name, "parameters": {},
        "workspace": {"strategy": "DEMO_TEMP"},
    })
    return payload["instance"]["instance_id"]


def _cleanup_instance(instance_id: str) -> None:
    try:
        consul_delete(f"jobflows/instances/{quote(instance_id, safe='')}", recurse=True)
    except Exception:
        pass


class TestWorkflowLogsBack:
    """执行日志页提供返回看板的固定入口。"""

    def test_back_link_returns_to_board(self, page: Page):
        group_id = "ui-nav-logs"
        page.goto(f"{BASE_URL}/#/groups/{group_id}/workflows/wf-nav/logs")
        page.wait_for_selector('a[data-testid], a', timeout=10000)
        assert _click_button_with_text(page, "返回看板")
        page.wait_for_timeout(500)
        assert page.evaluate("() => location.hash") == f"#/groups/{group_id}"


class TestBoardHeader:
    """看板头部：品牌回首页，已移除的按钮不再出现。"""

    def test_brand_click_returns_home(self, page: Page):
        page.goto(f"{BASE_URL}/#/groups/ui-nav-brand?tab=board")
        page.wait_for_selector('button[aria-label="返回项目首页"]')
        page.click('button[aria-label="返回项目首页"]')
        page.wait_for_timeout(500)
        assert page.evaluate("() => location.hash") == "#/"

    def test_removed_header_buttons_absent(self, page: Page):
        page.goto(f"{BASE_URL}/#/groups/ui-nav-clean?tab=board")
        page.wait_for_selector('button[aria-label="返回项目首页"]')
        content = page.content()
        assert "刷新" not in content
        assert page.query_selector('[data-testid="project-tabs"]') is None


class TestInstanceDetailBack:
    """实例详情返回所属模板详情。"""

    def test_back_goes_to_template_detail(self, page: Page):
        group_id = _create_group(page, "ui-nav-inst")
        try:
            template_id = _create_template(group_id, "nav-template")
            version = _api("POST", f"/api/templates/{template_id}/publish", {})
            instance_id = _create_instance(
                template_id, version["version"]["version_id"], "nav-instance")
            try:
                page.goto(f"{BASE_URL}/#/instances/{instance_id}")
                page.wait_for_timeout(1500)
                assert _click_button_with_text(page, "返回")
                page.wait_for_timeout(800)
                assert page.evaluate("() => location.hash") == \
                    f"#/templates/{template_id}"
            finally:
                _cleanup_instance(instance_id)
        finally:
            _cleanup_template(template_id)
            _cleanup_group(group_id)


class TestTemplateDetailBack:
    """模板详情返回所属项目详情（模板 tab）。"""

    def test_back_goes_to_project_detail(self, page: Page):
        group_id = _create_group(page, "ui-nav-tpl")
        try:
            template_id = _create_template(group_id, "nav-tpl-back")
            try:
                page.goto(f"{BASE_URL}/#/templates/{template_id}")
                page.wait_for_timeout(1500)
                assert _click_button_with_text(page, "返回")
                page.wait_for_timeout(800)
                assert page.evaluate("() => location.hash") == \
                    f"#/groups/{group_id}?tab=templates"
            finally:
                _cleanup_template(template_id)
        finally:
            _cleanup_group(group_id)


class TestTemplateBuilderBack:
    """模板编辑器：编辑态返回模板详情，新建态返回首页。"""

    def test_edit_back_goes_to_template_detail(self, page: Page):
        template_id = _create_template("unassigned", "nav-builder")
        try:
            page.goto(f"{BASE_URL}/#/templates/{template_id}/edit")
            page.wait_for_selector('[data-testid="builder-back"]', timeout=10000)
            page.click('[data-testid="builder-back"]')
            page.wait_for_timeout(800)
            assert page.evaluate("() => location.hash") == f"#/templates/{template_id}"
        finally:
            _cleanup_template(template_id)

    def test_new_back_goes_home(self, page: Page):
        page.goto(f"{BASE_URL}/#/templates/new")
        page.wait_for_selector('[data-testid="builder-back"]', timeout=10000)
        page.click('[data-testid="builder-back"]')
        page.wait_for_timeout(800)
        assert page.evaluate("() => location.hash") == "#/"


class TestSettingsBack:
    """项目与工作区配置页返回首页。"""

    def test_back_goes_home(self, page: Page):
        page.goto(f"{BASE_URL}/#/settings")
        page.wait_for_selector('[data-testid="settings-back"]', timeout=10000)
        page.click('[data-testid="settings-back"]')
        page.wait_for_timeout(800)
        assert page.evaluate("() => location.hash") == "#/"


class TestInstanceWizardWorkspace:
    """创建实例向导：非 DEMO_TEMP 策略必须选择项目工作区。"""

    def test_workspace_required_hint_and_disabled_submit(self, page: Page):
        # unassigned 组没有项目工作区 → 向导应提示并阻止提交
        template_id = _create_template("unassigned", "nav-wizard")
        try:
            _api("POST", f"/api/templates/{template_id}/publish", {})
            page.goto(f"{BASE_URL}/#/templates/{template_id}/instances/new")
            page.wait_for_selector('[data-testid="wizard-next"]')
            page.click('[data-testid="wizard-next"]')  # step 1 → 2
            page.wait_for_timeout(500)
            content = page.content()
            assert "启动时需要指定项目工作区" in content
            assert page.query_selector('[data-testid="wizard-workspace-select"]') is None

            # 选 DEMO_TEMP 后提示消失
            page.click('[data-testid="wizard-workspace"][value="DEMO_TEMP"]')
            page.wait_for_timeout(300)
            assert "启动时需要指定项目工作区" not in page.content()

            # 切回 ORIGINAL 进入确认页：提交按钮禁用
            page.click('[data-testid="wizard-workspace"][value="ORIGINAL"]')
            page.wait_for_timeout(300)
            page.click('[data-testid="wizard-next"]')  # step 2 → 3
            page.wait_for_timeout(500)
            assert page.evaluate(
                "() => document.querySelector('[data-testid=wizard-submit]')?.disabled"
            ) is True
        finally:
            _cleanup_template(template_id)
