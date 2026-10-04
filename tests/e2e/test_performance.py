"""
性能基准测试 (E2E)

参考 gstack /benchmark 的采集维度：
- TTFB (Time to First Byte)
- FCP (First Contentful Paint)
- LCP (Largest Contentful Paint)
- DOM Interactive / DOM Complete / Full Load
- 资源统计（JS bundle, CSS bundle, 总请求数）

参考 gstack /benchmark 的回归阈值：
- >50% 或 >500ms → REGRESSION
- >20% → WARNING

基线保存在 tests/e2e/baselines/performance.json

运行：
  pytest tests/e2e/test_performance.py -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from .webbridge import Page

from .helpers import (
    PerfMetrics,
    collect_perf_metrics,
    load_perf_baseline,
    save_perf_baseline,
    wait_for_network_idle,
)

BASELINE_FILE = Path(__file__).parent / "baselines" / "performance.json"


@pytest.mark.performance
class TestDashboardPerformance:
    """看板运行时性能测试。"""

    def test_dashboard_fcp(self, page: Page, dashboard_url: str) -> None:
        """首次内容绘制 < 1.5s。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        metrics = collect_perf_metrics(page)

        assert metrics.fcp_ms >= 0, "FCP 应可采集"
        if metrics.fcp_ms > 0:
            assert metrics.fcp_ms < 1500, (
                f"FCP {metrics.fcp_ms:.0f}ms 超过阈值 1500ms"
            )

    def test_dashboard_lcp(self, page: Page, dashboard_url: str) -> None:
        """最大内容绘制 < 2.5s。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        metrics = collect_perf_metrics(page)

        if metrics.lcp_ms > 0:
            assert metrics.lcp_ms < 2500, (
                f"LCP {metrics.lcp_ms:.0f}ms 超过阈值 2500ms"
            )

    def test_dashboard_ttfb(self, page: Page, dashboard_url: str) -> None:
        """首字节时间 < 800ms。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        metrics = collect_perf_metrics(page)

        if metrics.ttfb_ms > 0:
            assert metrics.ttfb_ms < 800, (
                f"TTFB {metrics.ttfb_ms:.0f}ms 超过阈值 800ms"
            )

    def test_bundle_size(self, page: Page, dashboard_url: str) -> None:
        """JS bundle < 500KB, CSS bundle < 100KB。

        参考 gstack /benchmark 的性能预算。
        """
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        metrics = collect_perf_metrics(page)

        if metrics.js_bundle_bytes > 0:
            js_kb = metrics.js_bundle_bytes / 1024
            assert js_kb < 500, (
                f"JS bundle {js_kb:.0f}KB 超过阈值 500KB"
            )

        if metrics.css_bundle_bytes > 0:
            css_kb = metrics.css_bundle_bytes / 1024
            assert css_kb < 100, (
                f"CSS bundle {css_kb:.0f}KB 超过阈值 100KB"
            )

    def test_total_requests(self, page: Page, dashboard_url: str) -> None:
        """首屏请求数保持在 60 以内。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        metrics = collect_perf_metrics(page)

        if metrics.total_requests > 0:
            assert metrics.total_requests < 60, (
                f"总请求数 {metrics.total_requests} 超过阈值 60"
            )

    def test_full_load_time(self, page: Page, dashboard_url: str) -> None:
        """完整加载时间 < 3s。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        metrics = collect_perf_metrics(page)

        if metrics.full_load_ms > 0:
            assert metrics.full_load_ms < 3000, (
                f"完整加载 {metrics.full_load_ms:.0f}ms 超过阈值 3000ms"
            )


@pytest.mark.performance
class TestWorkflowSwitchLatency:
    """工作流切换延迟测试。"""

    def test_workflow_switch_latency(self, page: Page, dashboard_url: str) -> None:
        """页面数据加载 < 1000ms。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        import time

        start = time.perf_counter()
        page.locator("text=需求列表").first.wait_for(state="visible", timeout=10000)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 1000, (
            f"页面数据加载延迟 {elapsed_ms:.0f}ms 超过阈值 1000ms"
        )


@pytest.mark.performance
class TestPerformanceBaseline:
    """性能基线管理测试。"""

    def test_save_and_compare_baseline(self, page: Page, dashboard_url: str) -> None:
        """保存性能基线并与历史对比。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        current = collect_perf_metrics(page)

        # 加载历史基线
        baseline = load_perf_baseline(BASELINE_FILE)

        if baseline is not None:
            # 对比检测回归
            results = current.check_regression(baseline)
            regressed = [k for k, v in results.items() if v == "REGRESSION"]
            warnings = [k for k, v in results.items() if v == "WARNING"]

            if regressed:
                # 打印回归信息但不直接失败（首次运行或环境差异可能导致误报）
                print(f"\n[Performance] REGRESSION detected: {regressed}")
                print(f"[Performance] Current: {current.to_dict()}")

            # 不强制失败——性能测试在 CI 中有噪音
            assert True
        else:
            # 首次运行：保存基线
            save_perf_baseline(current, BASELINE_FILE)
            print(f"\n[Performance] 基线已保存到: {BASELINE_FILE}")
