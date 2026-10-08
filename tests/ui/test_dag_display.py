"""UI test cases: DAG rendering on template editor and instance detail pages."""
from __future__ import annotations

import pytest
import time

from tests.e2e.webbridge import Page

BASE_URL = "http://127.0.0.1:3000"


def _create_template(page: Page, name: str, tasks_json: str) -> str:
    unique_name = f"{name}-{time.time_ns()}"
    page.goto(BASE_URL + "/#/templates/new")
    page.wait_for_selector('[data-testid="template-name"]')
    page.fill('[data-testid="template-name"]', unique_name)
    page.fill('[data-testid="tasks-editor"]', tasks_json)
    page.click('[data-testid="save-draft"]')
    page.wait_for_selector('[data-testid="builder-message"], [role="alert"]')
    url = page.evaluate("window.location.hash")
    return url


class TestTemplateEditorDag:
    """TASK-009/010 DAG preview on template editor page."""

    def test_dag_preview_renders_on_valid_json(self, page: Page):
        """UI-01: 编辑页面输入有效 JSON 后右侧显示 DAG 预览。"""
        _create_template(page, "dag-test-1",
            '[{"id":"build","name":"构建","depends_on":[]},{"id":"test","name":"测试","depends_on":["build"]}]')
        page.goto(page.evaluate("window.location.href"))
        page.wait_for_selector('[data-testid="dag-preview"]')
        assert page.query_selector('[data-testid="dag-preview"] .vue-flow') is not None

    def test_dag_preview_shows_node_count(self, page: Page):
        """UI-02: DAG 预览头部显示节点数。"""
        _create_template(page, "dag-test-2",
            '[{"id":"a","depends_on":[]},{"id":"b","depends_on":["a"]},{"id":"c","depends_on":["a"]}]')
        page.goto(page.evaluate("window.location.href"))
        page.wait_for_selector('[data-testid="dag-preview"]')
        text = page.content()
        assert "3 个节点" in text

    def test_dag_preview_parse_error_fallback(self, page: Page):
        """UI-03: JSON 格式无效时显示错误提示，不白屏。"""
        _create_template(page, "dag-test-3", "invalid json{{{")
        page.wait_for_selector('[data-testid="dag-preview"]')
        text = page.content()
        assert "暂不可用" in text or "无效" in text

    def test_dag_preview_updates_on_json_change(self, page: Page):
        """UI-04: 修改 JSON 后 DAG 预览节点数实时更新。"""
        _create_template(page, "dag-test-4",
            '[{"id":"a","depends_on":[]}]')
        page.goto(page.evaluate("window.location.href"))
        page.wait_for_selector('[data-testid="dag-preview"]')
        page.fill('[data-testid="tasks-editor"]',
            '[{"id":"a","depends_on":[]},{"id":"b","depends_on":["a"]}]')
        page.wait_for_timeout(500)
        text = page.content()
        assert "2 个节点" in text


class TestInstanceDetailDag:
    """Instance detail page DAG rendering with task status."""

    def test_instance_dag_renders(self, page: Page):
        """UI-05: 实例详情页显示运行 DAG 画布。"""
        page.goto(BASE_URL + "/#/instances")
        page.wait_for_timeout(1000)
        links = page.query_selector_all('[data-testid="instance-task"]')
        if not links:
            pytest.skip("No instances available")
        links[0].click()
        page.wait_for_selector('[data-testid="instance-dag"]')
        assert page.query_selector('[data-testid="instance-dag"] .vue-flow') is not None

    def test_instance_dag_empty_state(self, page: Page):
        """UI-06: 实例无任务时显示空状态提示。"""
        page.goto(BASE_URL + "/#/instances/nonexistent")
        page.wait_for_selector('[role="alert"]')
        assert page.query_selector('[role="alert"]') is not None
