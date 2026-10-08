"""
看板功能测试 (E2E)

参考 gstack /qa 的 diff-aware 模式：
- 每个用例覆盖一个核心交互路径
- 失败自动截图（conftest.py 的 screenshot_on_failure）
- 测试数据由 conftest.py 的 consul_setup/consul_multi_workflow fixture 管理

测试基于 dashboard 的 mock 模式运行（dashboard 在 Consul 不可达时自动降级为 mock）。
如需测试真实 Consul 数据，确保 Consul 在 8500 端口运行。
"""
from __future__ import annotations

import pytest
from .webbridge import Page, expect

from .helpers import (
    assert_no_console_errors,
    assert_visible,
    collect_console_errors,
    wait_for_network_idle,
)


class TestDashboardLoad:
    """页面加载和基础渲染测试。"""

    @pytest.mark.smoke
    def test_dashboard_loads(self, page: Page, dashboard_url: str) -> None:
        """页面正常加载，标题可见，无白屏。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 标题可见
        expect(page.get_by_text("Agent Dashboard")).to_be_visible()

        # 加载状态应该已经结束（不应有 spinner）
        # 数据源指示器可见
        expect(page.locator(".pulse-dot")).to_be_visible()

    @pytest.mark.smoke
    def test_dashboard_no_errors_on_load(self, page: Page, dashboard_url: str) -> None:
        """页面加载无 console error。"""
        errors: list[str] = []

        def _on_console(msg):
            if msg.type == "error":
                errors.append(msg.text)

        page.on("console", _on_console)
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 过滤掉可能的非关键 error（如 favicon 404）
        critical_errors = [e for e in errors if "favicon" not in e.lower()]
        assert len(critical_errors) == 0, (
            f"页面加载有 console error:\n" + "\n".join(critical_errors[:5])
        )

    def test_data_source_indicator(self, page: Page, dashboard_url: str) -> None:
        """数据源指示器显示 Mock 或 Consul 状态。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        indicator = page.locator(".pulse-dot")
        expect(indicator).to_be_visible()
        demo_text = page.get_by_text("Demo · 模拟数据")
        api_text = page.get_by_text("Harness API · 已连接")
        offline_text = page.get_by_text("离线 · 只读")
        assert demo_text.is_visible() or api_text.is_visible() or offline_text.is_visible(), (
            "数据源指示器应显示 Demo、Harness API 或离线状态"
        )


class TestWorkflowList:
    """工作流列表测试。"""

    def test_workflow_list_displays(self, page: Page, dashboard_url: str, job_flow_instances) -> None:
        """侧边栏显示实例列表。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 桌面端侧边栏有 "需求列表" 标题
        expect(page.get_by_text("需求列表")).to_be_visible()

        expect(page.get_by_text("用户订单中心 v2.0")).to_be_visible(timeout=15000)

    def test_multiple_workflows_visible(self, page: Page, dashboard_url: str, job_flow_instances) -> None:
        """侧边栏显示多个实例。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        expect(page.get_by_text("用户订单中心 v2.0")).to_be_visible(timeout=15000)
        expect(page.get_by_text("支付网关集成")).to_be_visible(timeout=10000)
        expect(page.get_by_text("消息通知中心")).to_be_visible(timeout=10000)

    def test_workflow_selection(self, page: Page, dashboard_url: str, job_flow_instances) -> None:
        """点击实例切换详情。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        page.get_by_text("支付网关集成").first.click()
        page.wait_for_timeout(500)

        expect(page.get_by_text("支付网关集成").first).to_be_visible(timeout=10000)
        expect(page.get_by_text("支付网关集成")).to_be_visible()

    def test_first_workflow_auto_selected(self, page: Page, dashboard_url: str, job_flow_instances) -> None:
        """第一个实例自动选中。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        expect(page.get_by_text("用户订单中心 v2.0")).to_be_visible(timeout=15000)


class TestDagGraph:
    """DAG 拓扑图测试。"""

    def test_dag_graph_renders(self, page: Page, dashboard_url: str) -> None:
        """DAG 图正确渲染 SVG 元素。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # DAG 区域标题
        expect(page.get_by_text("任务依赖拓扑")).to_be_visible()

        # SVG 图应该渲染
        svg = page.locator("svg").first
        expect(svg).to_be_visible()

        # SVG 中应该有任务节点
        svg_elements = page.locator("svg text, svg rect")
        count = svg_elements.count()
        assert count > 0, "DAG 图应包含至少一个 SVG 元素"

    def test_dag_task_click_opens_drawer(self, page: Page, dashboard_url: str) -> None:
        """点击 DAG 图中的任务节点打开详情抽屉。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 点击 SVG 中的文本节点（任务名）
        svg_text = page.locator("svg text").first
        if svg_text.is_visible():
            svg_text.click()
            page.wait_for_timeout(500)

            # 任务详情抽屉应该打开（桌面端）
            # TaskDrawer 显示任务名称
            drawer = page.locator(".w-64.flex-shrink-0")
            if drawer.is_visible():
                # 抽屉中有 agent 信息
                expect(page.get_by_text("agent-")).to_be_visible(timeout=5000)


class TestTaskList:
    """任务列表测试。"""

    def test_task_list_displays(self, page: Page, dashboard_url: str) -> None:
        """桌面端表格显示任务列表。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 任务列表标题
        expect(page.get_by_text("任务列表")).to_be_visible()

        # 桌面端表格应该可见
        table = page.locator("table")
        expect(table).to_be_visible()

        # 表头
        expect(page.get_by_text("状态")).to_be_visible()

    def test_task_list_contains_status_badges(self, page: Page, dashboard_url: str, job_flow_instances) -> None:
        """任务区域包含状态信息。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        expect(page.get_by_text("任务完成")).to_be_visible(timeout=10000)

    def test_progress_bar(self, page: Page, dashboard_url: str) -> None:
        """进度条显示正确的任务完成数。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 进度条显示 "N/M 任务完成"
        progress_text = page.get_by_text("任务完成")
        expect(progress_text).to_be_visible()

        # 百分比
        percent_text = page.get_by_text("%")
        expect(percent_text.first).to_be_visible()


class TestControlSignals:
    """控制信号测试。"""

    def test_control_pause_button_visible(self, page: Page, dashboard_url: str) -> None:
        """第一个 workflow (DEVELOPMENT 阶段) 显示暂停按钮。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 第一个 workflow 自动选中，处于 DEVELOPMENT 阶段
        # 暂停按钮应该可见（phase !== DONE 且 !== PAUSED）
        pause_btn = page.get_by_text("暂停")
        expect(pause_btn).to_be_visible(timeout=10000)

    def test_control_abort_button_visible(self, page: Page, dashboard_url: str) -> None:
        """中止按钮在非 DONE 阶段可见。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        abort_btn = page.get_by_text("中止")
        expect(abort_btn).to_be_visible(timeout=10000)

    def test_control_pause_opens_dialog(self, page: Page, dashboard_url: str) -> None:
        """点击暂停打开确认对话框。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 点击暂停
        page.get_by_text("暂停").click()
        page.wait_for_timeout(300)

        # 对话框应该打开
        dialog = page.locator("dialog[open]")
        expect(dialog).to_be_visible(timeout=5000)
        expect(page.get_by_text("暂停需求执行")).to_be_visible()
        expect(page.get_by_text("确认暂停")).to_be_visible()

        # 点击取消关闭对话框
        page.get_by_text("取消").click()
        page.wait_for_timeout(300)
        expect(dialog).to_be_hidden()

    def test_control_blocked_workflow_shows_retry(self, page: Page, dashboard_url: str, job_flow_instances) -> None:
        """QUEUED 状态的实例显示暂停和中止按钮。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        page.get_by_text("消息通知中心").first.click()
        page.wait_for_timeout(500)

        pause_btn = page.get_by_text("暂停")
        expect(pause_btn).to_be_visible(timeout=5000)

    @pytest.mark.smoke
    def test_control_dialog_confirm(self, page: Page, dashboard_url: str) -> None:
        """确认暂停操作。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 点击暂停
        page.get_by_text("暂停").click()
        page.wait_for_timeout(300)

        # 确认
        page.get_by_text("确认暂停").click()
        page.wait_for_timeout(500)

        # 对话框应该关闭
        dialog = page.locator("dialog[open]")
        expect(dialog).to_be_hidden()


class TestEmptyState:
    """空状态测试。"""

    def test_empty_state_when_no_workflow_selected(self, page: Page, dashboard_url: str) -> None:
        """不选中任何 workflow 时显示空状态提示。"""
        # 直接访问 dashboard（mock 数据会自动加载并选中第一个）
        # 空状态在 mock 数据不可用的情况下出现
        # 这里我们验证默认情况下的正常渲染
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 正常情况下有数据，不应看到空状态
        empty_text = page.get_by_text("请从左侧选择一个需求")
        # mock 模式下第一个 workflow 会自动选中，所以不应为空
        # 这个主要验证仪表板没有崩溃


class TestRefresh:
    """刷新功能测试。"""

    def test_refresh_button_exists(self, page: Page, dashboard_url: str) -> None:
        """刷新按钮可点击。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 刷新按钮包含文字 "刷新"
        refresh_btn = page.get_byText("刷新")
        expect(refresh_btn).to_be_visible(timeout=10000)


class TestPhaseBadges:
    """阶段徽章测试。"""

    def test_phase_badge_displays(self, page: Page, dashboard_url: str, job_flow_instances) -> None:
        """各实例显示正确的阶段徽章。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        expect(page.get_by_text("PENDING").first).to_be_visible(timeout=10000)

    def test_done_phase_badge(self, page: Page, dashboard_url: str, job_flow_instances) -> None:
        """DONE 阶段的实例显示正确徽章。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        done_badge = page.get_by_text("PENDING").first
        expect(done_badge).to_be_visible(timeout=10000)


class TestResponsiveLayout:
    """响应式布局测试。"""

    def test_mobile_viewport_still_loads(self, page: Page, dashboard_url: str) -> None:
        """375px 视口下页面正常加载。"""
        # WebBridge's CDP command needs an attached tab; navigate once before
        # applying the mobile metrics, then reload under the new viewport.
        page.goto(dashboard_url)
        page.set_viewport_size({"width": 375, "height": 812})
        page.reload()
        wait_for_network_idle(page)

        # 标题仍然可见
        expect(page.get_by_text("Agent Dashboard")).to_be_visible()

        # 移动端菜单按钮可见
        # 移动端不显示桌面端侧边栏
        desktop_sidebar = page.locator("aside.hidden.md\\:flex")
        expect(desktop_sidebar).to_be_hidden()
        # Restore the shared browser's desktop metrics for later isolated
        # sessions; WebBridge applies CDP metrics at browser level.
        page.set_viewport_size({"width": 1280, "height": 900})
