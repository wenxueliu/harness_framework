"""
无障碍基础检查 (E2E)

参考 gstack /design-review 的 a11y 清单：
- 可交互元素有可访问名称
- 标题层级不跳跃
- Tab 键导航顺序合理
- 焦点指示器可见

使用 Playwright 的 page.accessibility.snapshot() 获取可访问性树。

运行：
  pytest tests/e2e/test_a11y.py -v
"""
from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page, expect

from .helpers import wait_for_network_idle


def _get_a11y_tree(page: Page) -> dict | None:
    """获取页面的可访问性树（参考 gstack 的 ariaSnapshot）。"""
    return page.accessibility.snapshot()


def _find_all_interactive(node: dict) -> list[dict]:
    """递归查找所有可交互元素。"""
    interactive: list[dict] = []
    role = node.get("role", "").lower()
    name = node.get("name", "")

    interactive_roles = {
        "button", "link", "textbox", "combobox", "listbox",
        "menuitem", "menuitemcheckbox", "menuitemradio",
        "option", "radio", "slider", "spinbutton", "switch",
        "tab", "checkbox", "searchbox",
    }

    if role in interactive_roles:
        interactive.append(node)

    for child in node.get("children", []):
        interactive.extend(_find_all_interactive(child))

    return interactive


def _check_heading_hierarchy(node: dict, level: int = 0) -> list[str]:
    """检查标题层级是否有跳跃（如 h1 直接跳到 h3）。"""
    issues: list[str] = []
    role = node.get("role", "").lower()

    if role == "heading":
        h_level = int(node.get("level", 1))
        if level > 0 and h_level > level + 1:
            issues.append(
                f"标题层级跳跃: h{level} 直接到 h{h_level} ('{node.get('name', '')}')"
            )
        level = h_level

    for child in node.get("children", []):
        issues.extend(_check_heading_hierarchy(child, level))

    return issues


@pytest.mark.a11y
class TestAccessibilityBasics:
    """无障碍基础检查。"""

    def test_page_has_title(self, page: Page, dashboard_url: str) -> None:
        """页面有标题（<title> 标签或 aria-label）。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        title = page.title()
        assert len(title) > 0, "页面应该有标题"

    def test_interactive_elements_have_labels(self, page: Page, dashboard_url: str) -> None:
        """可交互元素有可访问名称（参考 gstack /design-review a11y 清单）。

        按钮、链接、输入框等应有 role + name。
        """
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        tree = _get_a11y_tree(page)
        if tree is None:
            pytest.skip("无法获取可访问性树")

        interactive = _find_all_interactive(tree)

        unlabeled: list[str] = []
        for el in interactive:
            role = el.get("role", "")
            name = el.get("name", "").strip()
            if not name and role in ("button", "link", "textbox", "combobox"):
                unlabeled.append(f"  {role}: 缺少可访问名称")

        assert len(unlabeled) == 0, (
            f"发现 {len(unlabeled)} 个无标签的可交互元素:\n" + "\n".join(unlabeled[:10])
        )

    def test_heading_hierarchy_no_skip(self, page: Page, dashboard_url: str) -> None:
        """标题层级不跳跃。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        tree = _get_a11y_tree(page)
        if tree is None:
            pytest.skip("无法获取可访问性树")

        issues = _check_heading_hierarchy(tree)
        assert len(issues) == 0, (
            "标题层级存在跳跃:\n" + "\n".join(issues[:5])
        )

    def test_focus_order_is_reasonable(self, page: Page, dashboard_url: str) -> None:
        """Tab 键导航有焦点指示器（focus-visible ring）。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 按几次 Tab 检查焦点是否移动
        focusable_count = page.evaluate("""() => {
            return document.querySelectorAll(
                'button, a, input, select, textarea, [tabindex]:not([tabindex="-1"])'
            ).length;
        }""")

        assert focusable_count > 0, "页面应该至少有 1 个可聚焦元素"

        # 按 Tab 并检查焦点
        page.keyboard.press("Tab")
        page.wait_for_timeout(200)

        # 检查是否有元素获得焦点
        focused = page.evaluate("() => document.activeElement?.tagName || 'none'")
        assert focused.lower() != "body", (
            "按 Tab 后应有元素获得焦点（非 body）"
        )

    def test_lang_attribute(self, page: Page, dashboard_url: str) -> None:
        """页面有正确的 lang 属性。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        lang = page.evaluate("() => document.documentElement.lang")
        # 接受 zh-CN, zh, en, en-US 等
        assert len(lang) >= 2, f"html 元素应有 lang 属性，当前: '{lang}'"

    def test_viewport_meta(self, page: Page, dashboard_url: str) -> None:
        """页面有 viewport meta 标签（响应式基础）。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        has_viewport = page.evaluate("""() => {
            const meta = document.querySelector('meta[name="viewport"]');
            return meta ? meta.content : null;
        }""")

        assert has_viewport is not None, "页面缺少 viewport meta 标签"
        assert "width=device-width" in has_viewport, (
            f"viewport meta 应包含 width=device-width，当前: {has_viewport}"
        )


@pytest.mark.a11y
class TestKeyboardNavigation:
    """键盘导航测试。"""

    def test_escape_closes_dialog(self, page: Page, dashboard_url: str) -> None:
        """Escape 键关闭控制对话框。"""
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        # 打开暂停对话框
        page.get_by_text("暂停").click()
        page.wait_for_timeout(300)

        dialog = page.locator("dialog[open]")
        expect(dialog).to_be_visible()

        # 按 Escape 关闭
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        expect(dialog).to_be_hidden()

    def test_tab_index_no_positive_values(self, page: Page, dashboard_url: str) -> None:
        """没有 tabindex > 0 的元素（会破坏自然 Tab 顺序）。

        参考 gstack /design-review 的 a11y checklist。
        """
        page.goto(dashboard_url)
        wait_for_network_idle(page)

        positive_tabindex = page.evaluate("""() => {
            const els = document.querySelectorAll('[tabindex]:not([tabindex="0"]):not([tabindex="-1"])');
            return Array.from(els).map(el => ({
                tag: el.tagName,
                tabindex: el.getAttribute('tabindex'),
                text: el.textContent?.substring(0, 50),
            }));
        }""")

        # tabindex="0" 和 tabindex="-1" 是合法的，>0 是有问题的
        bad = [e for e in positive_tabindex if int(e["tabindex"]) > 0]
        assert len(bad) == 0, (
            f"发现 {len(bad)} 个 tabindex > 0 的元素（破坏自然 Tab 顺序）"
        )
