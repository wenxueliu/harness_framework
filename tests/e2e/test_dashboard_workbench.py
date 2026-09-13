"""UI automation for the workspace-first Dashboard entry points."""
from __future__ import annotations

import pytest

from .webbridge import Page, expect
from .helpers import wait_for_network_idle


@pytest.mark.e2e
@pytest.mark.smoke
def test_task_drawer_links_to_workbench(page: Page, dashboard_url: str, consul_setup: str) -> None:
    page.goto(f"{dashboard_url}/#/groups/unassigned/workflows/{consul_setup}")
    wait_for_network_idle(page)
    rows = page.locator("tbody tr").first
    rows.wait_for(timeout=10000)
    rows.click()
    page.wait_for_timeout(300)
    expect(page.get_by_text("Task Workbench")).to_be_visible(timeout=5000)
    expect(page.get_by_text("backend")).to_be_visible(timeout=5000)


@pytest.mark.e2e
def test_settings_and_logs_routes_are_real_pages(page: Page, dashboard_url: str) -> None:
    page.goto(dashboard_url + "/#/settings")
    wait_for_network_idle(page)
    expect(page.get_by_text("项目与工作区配置")).to_be_visible(timeout=10000)
    page.goto(dashboard_url + "/#/groups/unassigned/workflows/REQ-2026-001/logs")
    wait_for_network_idle(page)
    expect(page.get_by_text("执行日志")).to_be_visible(timeout=10000)
