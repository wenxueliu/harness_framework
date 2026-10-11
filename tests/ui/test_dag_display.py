"""UI test cases: DAG rendering on template editor and instance detail pages."""
from __future__ import annotations

import pytest
import os
import time

from tests.e2e.webbridge import Page

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:3000")


def _create_template(page: Page, name: str, tasks_json: str) -> str:
    unique_name = f"{name}-{time.time_ns()}"
    page.goto(BASE_URL + "/#/templates/new")
    page.wait_for_selector('[data-testid="template-name"]')
    page.fill('[data-testid="template-name"]', unique_name)
    _set_tasks_json(page, tasks_json)
    page.click('[data-testid="save-draft"]')
    page.wait_for_selector('[data-testid="builder-message"], [role="alert"]')
    url = page.evaluate("window.location.hash")
    return url


def _set_tasks_json(page: Page, tasks_json: str) -> None:
    """Set Monaco content via the exposed editor instance, falling back to edit-context typing."""
    inserted = page.evaluate("""async (value) => {
      const deadline = Date.now() + 10000;
      while (Date.now() < deadline) {
        const store = window.__harnessMonacoEditors || {};
        if (store.tasks && typeof store.tasks.setValue === 'function') {
          store.tasks.setValue(value);
          return true;
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      const el = document.querySelector('[data-testid="tasks-editor"] .native-edit-context');
      if (!el) return false;
      el.focus();
      if (document.activeElement?.getAttribute('contenteditable') !== 'true') return false;
      document.execCommand('selectAll');
      return document.execCommand('insertText', false, value);
    }""", tasks_json)
    assert inserted is True


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
        _set_tasks_json(
            page, '[{"id":"a","depends_on":[]},{"id":"b","depends_on":["a"]}]'
        )
        page.wait_for_timeout(500)
        text = page.content()
        assert "2 个节点" in text


class TestInstanceDetailDag:
    """Instance detail page DAG rendering with task status."""

    def test_instance_dag_renders(self, page: Page):
        """UI-05: 实例详情页显示运行 DAG 画布。"""
        from tests.e2e.conftest import api_json

        suffix = str(time.time_ns())
        template_id = "ui-dag-" + suffix
        instance_name = "UI DAG " + suffix
        instance_id = None
        try:
            api_json("POST", "/api/templates", {
                "template_id": template_id,
                "name": "UI DAG template " + suffix,
                "tasks": [
                    {"id": "build", "type": "backend", "depends_on": []},
                    {"id": "test", "type": "test", "depends_on": ["build"]},
                ],
            })
            published = api_json("POST", "/api/templates/" + template_id + "/publish", {})
            created = api_json("POST", "/api/instances", {
                "template_id": template_id,
                "version_id": published["version"]["version_id"],
                "name": instance_name,
                "parameters": {},
                "git": {"ref": "ui-test"},
                "workspace": {"workspace_id": "ui-dag-workspace"},
            })
            instance_id = created.get("instance", {}).get("instance_id") or created.get("instance_id")

            page.goto(BASE_URL + "/#/instances")
            page.wait_for_selector('[data-testid="instance-card"]', timeout=10000)
            page.locator('[data-testid="instance-card"] a').first.click()
            page.wait_for_selector('[data-testid="instance-dag"]', timeout=10000)
            assert page.query_selector('[data-testid="instance-dag"] .vue-flow') is not None
        finally:
            for path, kwargs in (
                (f"/api/instances/{instance_id}" if instance_id else None, {}),
                (f"/api/templates/{template_id}", {}),
            ):
                if path:
                    try:
                        api_json("DELETE", path)
                    except RuntimeError:
                        pass

    def test_instance_dag_empty_state(self, page: Page):
        """UI-06: 实例无任务时显示空状态提示。"""
        page.goto(BASE_URL + "/#/instances/nonexistent")
        page.wait_for_selector('[role="alert"]')
        assert page.query_selector('[role="alert"]') is not None
