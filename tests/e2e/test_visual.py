"""
视觉回归测试 (E2E)

参考 gstack 的 screenshot + diff 模式：
- 使用 Kimi WebBridge 的真实浏览器截图
- 首次运行 --update-snapshots 生成基线，后续运行做对比
- 参考 AutoCLI 的图像匹配 threshold 模式（max_diff_pixel_ratio）

运行：
  # 首次运行：生成基线截图
  pytest tests/e2e/test_visual.py -v --update-snapshots

  # 后续运行：对比基线
  pytest tests/e2e/test_visual.py -v
"""
from __future__ import annotations

import pytest
from .webbridge import Page, expect

from .helpers import wait_for_network_idle


# 视觉对比容忍度（参考 gstack snapshot diff + AutoCLI threshold）
MAX_DIFF_PIXEL_RATIO = 0.01  # 1% 像素差异容忍度


class TestDashboardScreenshots:
    """看板全页截图对比。"""

    @pytest.mark.visual
    def test_dashboard_full_page(self, page: Page, dashboard_url: str) -> None:
        """整个看板页面的全页截图对比。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)
        # 确保加载完成
        page.wait_for_timeout(1000)

        expect(page).to_have_screenshot(
            path="tests/e2e/baselines/screenshots/dashboard_full.png",
            full_page=True,
            max_diff_pixel_ratio=MAX_DIFF_PIXEL_RATIO,
        )

    @pytest.mark.visual
    def test_dashboard_header(self, page: Page, dashboard_url: str) -> None:
        """顶部导航栏截图对比。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        header = page.locator("header")
        expect(header).to_have_screenshot(
            path="tests/e2e/baselines/screenshots/dashboard_header.png",
            max_diff_pixel_ratio=MAX_DIFF_PIXEL_RATIO,
        )


class TestWorkflowListView:
    """工作流列表视觉测试。"""

    @pytest.mark.visual
    def test_sidebar_workflow_list(self, page: Page, dashboard_url: str) -> None:
        """侧边栏工作流列表截图对比。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        sidebar = page.locator("aside.hidden.md\\:flex").first
        if sidebar.is_visible():
            expect(sidebar).to_have_screenshot(
                path="tests/e2e/baselines/screenshots/sidebar_workflow_list.png",
                max_diff_pixel_ratio=MAX_DIFF_PIXEL_RATIO,
            )
        else:
            pytest.skip("桌面端侧边栏不可见（移动视口或未渲染）")


class TestDagGraphVisual:
    """DAG 图视觉测试。"""

    @pytest.mark.visual
    def test_dag_graph(self, page: Page, dashboard_url: str) -> None:
        """DAG 拓扑图 SVG 渲染截图对比。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 滚动到 DAG 区域
        dag_section = page.get_by_text("任务依赖拓扑")
        if dag_section.is_visible():
            dag_section.scroll_into_view_if_needed()
            page.wait_for_timeout(300)

            dag_container = page.locator(".bg-card.border.border-border.rounded-lg")
            expect(dag_container.first).to_have_screenshot(
                path="tests/e2e/baselines/screenshots/dag_graph.png",
                max_diff_pixel_ratio=MAX_DIFF_PIXEL_RATIO,
            )


class TestTaskListVisual:
    """任务列表视觉测试。"""

    @pytest.mark.visual
    def test_task_table(self, page: Page, dashboard_url: str) -> None:
        """任务表格截图对比。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        task_section = page.get_by_text("任务列表")
        task_section.scroll_into_view_if_needed()
        page.wait_for_timeout(300)

        # 桌面端表格
        table = page.locator("table")
        if table.is_visible():
            expect(table).to_have_screenshot(
                path="tests/e2e/baselines/screenshots/task_table.png",
                max_diff_pixel_ratio=MAX_DIFF_PIXEL_RATIO,
            )

    @pytest.mark.visual
    def test_progress_bar(self, page: Page, dashboard_url: str) -> None:
        """进度条卡片截图对比。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        progress_card = page.get_by_text("任务完成").locator("..").locator("..")
        expect(progress_card).to_have_screenshot(
            path="tests/e2e/baselines/screenshots/progress_bar.png",
            max_diff_pixel_ratio=MAX_DIFF_PIXEL_RATIO,
        )


class TestTaskDetailVisual:
    """任务详情面板视觉测试。"""

    @pytest.mark.visual
    def test_task_drawer(self, page: Page, dashboard_url: str) -> None:
        """桌面端任务详情面板截图对比。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 点击表格中的任务行打开详情
        table_rows = page.locator("table tbody tr")
        if table_rows.first.is_visible():
            table_rows.first.click()
            page.wait_for_timeout(500)

            drawer = page.locator(".w-64.flex-shrink-0").first
            if drawer.is_visible():
                expect(drawer).to_have_screenshot(
                    path="tests/e2e/baselines/screenshots/task_drawer.png",
                    max_diff_pixel_ratio=MAX_DIFF_PIXEL_RATIO,
                )


class TestControlDialogVisual:
    """控制对话框视觉测试。"""

    @pytest.mark.visual
    def test_pause_dialog(self, page: Page, dashboard_url: str) -> None:
        """暂停确认对话框截图对比。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 打开暂停对话框
        page.get_by_text("暂停").click()
        page.wait_for_timeout(300)

        dialog = page.locator("dialog[open]")
        if dialog.is_visible():
            expect(dialog).to_have_screenshot(
                path="tests/e2e/baselines/screenshots/pause_dialog.png",
                max_diff_pixel_ratio=MAX_DIFF_PIXEL_RATIO,
            )

        # 关闭
        page.get_by_text("取消").click()
        page.wait_for_timeout(300)


class TestStatsVisual:
    """统计面板视觉测试。"""

    @pytest.mark.visual
    def test_stats_panel(self, page: Page, dashboard_url: str) -> None:
        """桌面端全局概览面板截图对比。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        stats_panel = page.locator("aside.xl\\:flex").first
        if stats_panel.is_visible():
            expect(stats_panel).to_have_screenshot(
                path="tests/e2e/baselines/screenshots/stats_panel.png",
                max_diff_pixel_ratio=MAX_DIFF_PIXEL_RATIO,
            )
        else:
            # 小屏幕上使用移动端概览
            page.set_viewport_size({"width": 375, "height": 812})
            page.reload()
            wait_for_network_idle(page)

            # 切换到概览 tab
            stats_tab = page.get_by_text("概览")
            if stats_tab.is_visible():
                stats_tab.click()
                page.wait_for_timeout(300)
                # 截取概览区域
                page.screenshot(
                    path="tests/e2e/baselines/screenshots/stats_panel.png",
                    full_page=True,
                )
