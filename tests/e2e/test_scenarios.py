"""
YAML 场景驱动的 E2E 测试

参考 AutoCLI (nashsu/AutoCLI) 的声明式 YAML Pipeline 模式：
- AutoCLI 用 YAML 定义数据抓取 adapter（selectors, pagination, transform）
- harness 用 YAML 定义 E2E 测试场景（steps, assertions, viewport）

这种声明式模式的优势：
- 非开发人员也可以编写测试场景
- 测试场景和测试逻辑分离
- 容易做 diff-aware 测试选择（只跑变更相关的场景）
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from playwright.sync_api import Page

from .helpers import wait_for_network_idle

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def _load_scenarios(yaml_file: str) -> list[dict[str, Any]]:
    """加载 YAML 中的测试场景。"""
    filepath = SCENARIOS_DIR / yaml_file
    if not filepath.exists():
        pytest.skip(f"场景文件不存在: {filepath}")
    data = yaml.safe_load(filepath.read_text())
    return data.get("scenarios", [])


def _run_step(page: Page, step: dict[str, Any], dashboard_url: str) -> None:
    """执行单个 YAML 步骤。

    参考 AutoCLI 的命令分发模式：每个 action 对应一个 handler。
    """
    action = step["action"]
    selector = step.get("selector", "")
    value = step.get("value", "")
    description = step.get("description", action)

    if action == "goto":
        page.goto(f"{dashboard_url}{value}" if value else dashboard_url)

    elif action == "wait":
        if value == "networkidle":
            wait_for_network_idle(page)
        else:
            try:
                ms = int(value)
                page.wait_for_timeout(ms)
            except ValueError:
                page.wait_for_timeout(2000)

    elif action == "click":
        page.locator(selector).first.click()
        page.wait_for_timeout(200)

    elif action == "fill":
        page.locator(selector).first.fill(str(value))

    elif action == "scroll_to":
        page.locator(selector).first.scroll_into_view_if_needed()

    elif action == "assert_visible":
        assert page.locator(selector).first.is_visible(timeout=10000), (
            f"[{description}] 元素不可见: {selector}"
        )

    elif action == "assert_hidden":
        assert page.locator(selector).first.is_hidden(timeout=5000), (
            f"[{description}] 元素可见但预期隐藏: {selector}"
        )

    elif action == "assert_not_visible":
        # 软断言：元素不存在于页面
        visible = page.locator(selector).first.is_visible(timeout=3000)
        if visible:
            print(f"  [WARNING] {description}: 元素意外可见 ({selector})")

    elif action == "assert_count_gt":
        count = page.locator(selector).count()
        assert count > int(value), (
            f"[{description}] 元素数量 {count} 不大于 {value}: {selector}"
        )

    elif action == "assert_text_contains":
        assert page.get_by_text(str(value)).first.is_visible(timeout=5000), (
            f"[{description}] 页面不含文本: {value}"
        )

    elif action == "screenshot":
        filename = value or f"scenario_{int(time.time())}.png"
        page.screenshot(path=str(SCENARIOS_DIR.parent / "reports" / filename))

    elif action == "evaluate":
        result = page.evaluate(value)
        print(f"  [evaluate] {description}: {json.dumps(result, default=str)[:200]}")

    elif action == "viewport":
        if isinstance(value, dict):
            page.set_viewport_size(value)
        else:
            w, h = value.split("x") if "x" in str(value) else (1280, 720)
            page.set_viewport_size({"width": int(w), "height": int(h)})

    else:
        raise ValueError(f"未知 action: {action}")


def _parametrize_scenarios(yaml_file: str):
    """从 YAML 文件生成参数化测试用例。

    参考 AutoCLI 的 adapter 发现模式：扫描目录自动注册。
    """
    scenarios = _load_scenarios(yaml_file)
    ids = [s["name"] for s in scenarios]
    return pytest.mark.parametrize(
        "scenario",
        scenarios,
        ids=ids,
    )


class TestFromYAML:
    """YAML 声明的 E2E 场景测试。

    参考 AutoCLI (nashsu/AutoCLI) 的声明式 YAML Pipeline：
    https://github.com/nashsu/AutoCLI
    """

    @_parametrize_scenarios("dashboard.yaml")
    def test_scenario(self, scenario: dict, page: Page, dashboard_url: str) -> None:
        """执行 YAML 定义的测试场景。"""
        # 应用 viewport 配置
        if "viewport" in scenario:
            vp = scenario["viewport"]
            page.set_viewport_size({"width": vp["width"], height: vp["height"]})

        # 执行步骤
        for i, step in enumerate(scenario.get("steps", [])):
            try:
                _run_step(page, step, dashboard_url)
            except Exception as e:
                # 失败时标注步骤信息
                raise AssertionError(
                    f"Scenario '{scenario['name']}' Step {i+1} failed: {step.get('description', step['action'])}\n"
                    f"  Action: {step['action']}\n"
                    f"  Selector: {step.get('selector', 'N/A')}\n"
                    f"  Error: {e}"
                ) from e
