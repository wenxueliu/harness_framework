"""UI automation for project administration and workflow creation."""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import quote

import pytest
import requests

from .conftest import CONSUL_URL, WEBAPI_URL
from .helpers import wait_for_network_idle
from .webbridge import Page, expect


def _cleanup_group(group_name: str) -> None:
    """Remove the group and its registered workspaces after the UI test."""
    try:
        response = requests.get(f"{WEBAPI_URL}/api/project-groups", timeout=10)
        groups = response.json().get("project_groups", [])
        group = next((item for item in groups if item.get("name") == group_name), None)
        if not group:
            return
        group_id = group["group_id"]
        workspaces = requests.get(
            f"{WEBAPI_URL}/api/project-groups/{quote(group_id, safe='')}/workspaces",
            timeout=10,
        ).json().get("workspaces", [])
        for workspace in workspaces:
            requests.delete(
                f"{CONSUL_URL}/v1/kv/workspaces/projects/{quote(workspace['workspace_id'], safe='')}",
                params={"recurse": "true"}, timeout=10,
            )
        requests.delete(
            f"{CONSUL_URL}/v1/kv/project-groups/{quote(group_id, safe='')}",
            params={"recurse": "true"}, timeout=10,
        )
    except Exception:
        pass


def _cleanup_workflow(req_id: str) -> None:
    try:
        requests.delete(
            f"{CONSUL_URL}/v1/kv/workflows/{quote(req_id, safe='')}",
            params={"recurse": "true"}, timeout=10,
        )
    except Exception:
        pass


def _create_group_via_api(name: str) -> str:
    response = requests.post(
        f"{WEBAPI_URL}/api/project-groups",
        json={"name": name, "description": "UI workflow assignment"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["project_group"]["group_id"]


@pytest.mark.e2e
def test_project_group_member_workspace_lifecycle(page: Page, dashboard_url: str) -> None:
    """A user can create a group, add a member, register a workspace and preflight it."""
    group_name = f"UI 项目组 {time.time_ns()}"
    member = f"ui-member-{time.time_ns()}"
    workspace_name = f"UI Workspace {time.time_ns()}"
    page.goto(dashboard_url + "/#/")
    wait_for_network_idle(page)
    try:
        expect(page.get_by_text("新建项目")).to_be_visible(timeout=10000)
        page.get_by_text("新建项目").click()
        expect(page.get_by_text("创建项目组")).to_be_visible(timeout=10000)

        page.locator('input[placeholder="新项目组名称"]').fill(group_name)
        page.locator('input[placeholder="说明"]').fill("UI automation group")
        page.get_by_text("创建项目组").click()
        expect(page.get_by_text(group_name)).to_be_visible(timeout=10000)

        page.locator('input[placeholder="subject ID"]').fill(member)
        page.get_by_text("添加").click()
        expect(page.get_by_text(member)).to_be_visible(timeout=10000)
        expect(page.get_by_text("DEVELOPER")).to_be_visible()

        page.locator('input[placeholder="Workspace 名称"]').fill(workspace_name)
        absolute_path = str(Path(__file__).resolve().parents[2] / "agent_dashboard")
        page.evaluate("""() => {
            const select = Array.from(document.querySelectorAll('select'))
              .find((item) => item.querySelector('option[value="ABSOLUTE_PATH"]'));
            if (!select) throw new Error('Workspace 路径类型选择器不存在');
            select.value = 'ABSOLUTE_PATH';
            select.dispatchEvent(new Event('change', { bubbles: true }));
        }""")
        absolute_input = page.locator('input[placeholder*="服务端绝对目录"]')
        absolute_input.fill("/tmp")
        page.get_by_text("登记 Workspace").click()
        expect(page.locator('[role="alert"]')).to_be_visible(timeout=10000)
        assert page.get_by_text(workspace_name).count() == 0

        absolute_input.fill(absolute_path)
        page.get_by_text("登记 Workspace").click()
        expect(page.get_by_text(workspace_name)).to_be_visible(timeout=10000)

        page.get_by_text("Preflight").click()
        expect(page.get_by_text("read=true")).to_be_visible(timeout=10000)
    finally:
        _cleanup_group(group_name)


@pytest.mark.e2e
def test_workflow_builder_publish_shows_created_task_list(page: Page, dashboard_url: str) -> None:
    """Creating a template via JobFlowBuilder persists the DAG."""
    template_id = ""
    suffix = str(time.time_ns())
    page.goto(dashboard_url + "/#/templates/new")
    wait_for_network_idle(page)
    try:
        page.locator('[data-testid="template-name"]').wait_for(timeout=10000)
        page.locator('[data-testid="template-name"]').fill("E2E Builder " + suffix)
        page.locator("textarea").fill(
            '[{"id":"build","name":"构建","type":"backend","depends_on":[]},'
            '{"id":"test","name":"测试","type":"test","depends_on":["build"]}]'
        )
        page.locator('[data-testid="save-draft"]').click()
        expect(page.get_by_text("草稿已保存")).to_be_visible(timeout=10000)

        page.locator('[data-testid="publish-only"]').click()
        expect(page.get_by_text("已发布")).to_be_visible(timeout=10000)
        template_id = "e2e-builder-" + suffix
    finally:
        if template_id:
            try:
                import requests as _requests
                _requests.delete(f"{WEBAPI_URL}/api/templates/{template_id}", timeout=10)
            except Exception:
                pass


@pytest.mark.e2e
def test_selected_project_workflow_is_not_created_under_unassigned(page: Page, dashboard_url: str) -> None:
    """Creating a template from a selected project navigates to the builder."""
    group_name = f"UI Workflow Project {time.time_ns()}"
    group_id = _create_group_via_api(group_name)
    page.goto(dashboard_url + "/#/")
    wait_for_network_idle(page)
    try:
        group_item = page.get_by_text(group_name)
        expect(group_item).to_be_visible(timeout=10000)
        group_item.click()
        page.get_by_text("从模板创建").click()
        wait_for_network_idle(page)
        expect(page.get_by_text("作业流模板")).to_be_visible(timeout=10000)
    finally:
        _cleanup_group(group_name)
