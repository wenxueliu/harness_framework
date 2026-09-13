"""End-to-end user journeys for the workspace-first Dashboard."""
from __future__ import annotations

import pytest

from .helpers import wait_for_network_idle
from .webbridge import Page, expect


def _journey_url(dashboard_url: str, journey: dict) -> str:
    return (
        f"{dashboard_url}/#/groups/{journey['group_id']}"
        f"/workflows/{journey['req_id']}"
    )


@pytest.mark.e2e
@pytest.mark.smoke
def test_user_journey_from_group_to_agent_intervention(
    page: Page, dashboard_url: str, workspace_user_journey: dict,
) -> None:
    """A user can find a workflow, inspect history, search files, and intervene."""
    page.goto(_journey_url(dashboard_url, workspace_user_journey))
    wait_for_network_idle(page)

    expect(page.get_by_text("Workspace 用户旅程")).to_be_visible(timeout=15000)
    expect(page.get_by_text("E2E Workspace")).to_be_visible(timeout=10000)

    rows = page.locator("table tbody tr")
    rows.first.wait_for(timeout=10000)
    # Selecting a task from the workflow view enters its stable Workbench URL.
    rows.first.click()
    expect(page.get_by_text("Task Workbench")).to_be_visible(timeout=10000)
    expect(page.get_by_text("backend")).to_be_visible()

    # The selected Attempt has real execution history and a workspace tree.
    attempt_select = page.locator('select[aria-label="Attempt"]')
    attempt_select.wait_for(timeout=10000)
    expect(page.get_by_text("Agent inspected workspace")).to_be_visible(timeout=10000)
    expect(page.get_by_text("hello-world.json")).to_be_visible(timeout=10000)

    # Search is a user-visible action, not a direct API assertion.
    search = page.locator('input[placeholder="搜索工作区文件"]')
    search.fill("hello")
    page.locator('button[title="搜索"]').click()
    expect(page.get_by_text("hello-world.json")).to_be_visible(timeout=10000)

    # A human can add context to the active Agent session.
    message = page.locator('textarea[placeholder="补充信息或修复说明"]')
    message.fill("请检查示例配置并保留当前修改。")
    page.get_by_text("发送").click()
    expect(page.get_by_text("已记录 1 条人工消息")).to_be_visible(timeout=10000)


@pytest.mark.e2e
def test_user_journey_attempt_diff_merge_and_navigation(
    page: Page, dashboard_url: str, workspace_user_journey: dict,
) -> None:
    """A user can compare Attempts, create/apply a merge, then inspect logs."""
    page.goto(_journey_url(dashboard_url, workspace_user_journey))
    wait_for_network_idle(page)
    rows = page.locator("table tbody tr")
    rows.first.wait_for(timeout=10000)
    rows.first.click()
    expect(page.get_by_text("Task Workbench")).to_be_visible(timeout=10000)

    attempt_select = page.locator('select[aria-label="Attempt"]')
    attempt_select.wait_for(timeout=10000)
    # Both immutable bindings are visible to the operator.
    assert attempt_select.count() == 1
    assert "attempt-journey-1" in attempt_select.page.evaluate(
        "s => document.querySelector(s)?.innerText || ''", attempt_select._css()
    )
    assert "attempt-journey-2" in attempt_select.page.evaluate(
        "s => document.querySelector(s)?.innerText || ''", attempt_select._css()
    )

    page.get_by_text("hello-world.json").first.click()
    page.get_by_text("Diff").click()
    expect(page.locator("pre").first).to_be_visible(timeout=10000)

    expect(page.get_by_text("Explicit Merge Task")).to_be_visible()
    page.get_by_text("创建").first.click()
    expect(page.get_by_text("PENDING")).to_be_visible(timeout=10000)
    page.get_by_text("应用合并").click()
    expect(page.get_by_text("DONE")).to_be_visible(timeout=10000)

    # Workbench navigation keeps the workflow context intact.
    page.get_by_text("执行日志").click()
    expect(page.get_by_text("执行日志")).to_be_visible(timeout=10000)
    page.get_by_text("全局配置").click()
    expect(page.get_by_text("项目与工作区配置")).to_be_visible(timeout=10000)
