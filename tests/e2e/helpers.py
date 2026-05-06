"""
E2E 测试辅助工具

参考 gstack 的核心模式：
- HealthScore   → gstack /qa 的 8 维加权健康分
- collect_console_errors → gstack "$B console --errors"
- collect_perf_metrics   → gstack "$B perf" (Web Vitals)
- take_annotated_screenshot → gstack "snapshot -a -o path.png"
- visual diff            → gstack "snapshot -D" 的 diff 对比思路

参考 AutoCLI：
- assert_visual_match → AutoCLI 的图像匹配 threshold 模式
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, expect


# ============================================================
# HealthScore — 参考 gstack /qa 的 8 维加权健康分
# ============================================================

@dataclass
class HealthScore:
    """综合健康评分。

    参考 gstack /qa SKILL.md 的 rubric：
    - Console (15%), Links (10%), Visual (10%)
    - Functional (20%), UX (15%), Performance (10%)
    - Content (5%), Accessibility (15%)
    """

    console: float = 100.0
    links: float = 100.0
    visual: float = 100.0
    functional: float = 100.0
    ux: float = 100.0
    performance: float = 100.0
    content: float = 100.0
    accessibility: float = 100.0

    _weights: dict[str, float] = field(default_factory=lambda: {
        "console": 0.15,
        "links": 0.10,
        "visual": 0.10,
        "functional": 0.20,
        "ux": 0.15,
        "performance": 0.10,
        "content": 0.05,
        "accessibility": 0.15,
    })

    @property
    def score(self) -> float:
        """0-100 加权平均分。"""
        return sum(
            getattr(self, cat) * weight
            for cat, weight in self._weights.items()
        )

    @property
    def grade(self) -> str:
        """字母等级 A-F。"""
        s = self.score
        if s >= 90:
            return "A"
        if s >= 80:
            return "B"
        if s >= 70:
            return "C"
        if s >= 60:
            return "D"
        return "F"

    def to_dict(self) -> dict[str, float]:
        return {
            "console": self.console,
            "links": self.links,
            "visual": self.visual,
            "functional": self.functional,
            "ux": self.ux,
            "performance": self.performance,
            "content": self.content,
            "accessibility": self.accessibility,
            "total": self.score,
        }

    def to_json(self, filepath: str | Path) -> None:
        """保存健康分为 JSON 文件（参考 gstack 的 baseline.json）。"""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))


# ============================================================
# Console Error 收集 — 参考 gstack "$B console --errors"
# ============================================================

def collect_console_errors(page: Page) -> list[str]:
    """收集页面上的 console.error 消息。

    参考 gstack /qa 的 console check：
    - 0 errors = 100
    - 1-3 errors = 70
    - 4-10 errors = 40
    - 10+ errors = 10
    """
    errors: list[str] = []

    def _on_console(msg):
        if msg.type == "error":
            errors.append(msg.text)

    page.on("console", _on_console)
    # 注意：这个 handler 在调用后立即生效，但不会收集之前的事件
    # 实际使用中在 page.goto() 之前注册
    return errors


def assert_no_console_errors(page: Page, max_allowed: int = 0) -> list[str]:
    """断言无 console error。返回收集到的 error 列表。

    max_allowed: 允许的最大 error 数（某些库会产生预期的 warning-level 消息）
    """
    errors: list[str] = []

    def _on_console(msg):
        if msg.type == "error":
            errors.append(msg.text)

    page.on("console", _on_console)

    # 触发一次 JS 执行以获取已缓冲的错误
    try:
        page.evaluate("() => console.log('__e2e_health_check__')")
    except Exception:
        pass

    if len(errors) > max_allowed:
        error_detail = "\n".join(errors[:10])
        raise AssertionError(
            f"检测到 {len(errors)} 个 console error（允许最多 {max_allowed}）:\n{error_detail}"
        )

    return errors


# ============================================================
# 性能指标采集 — 参考 gstack "$B perf" (Web Vitals)
# ============================================================

@dataclass
class PerfMetrics:
    """页面性能指标。参考 gstack /benchmark 的采集维度。"""

    ttfb_ms: float = 0.0
    fcp_ms: float = 0.0
    lcp_ms: float = 0.0
    dom_interactive_ms: float = 0.0
    dom_complete_ms: float = 0.0
    full_load_ms: float = 0.0
    total_requests: int = 0
    total_transfer_bytes: int = 0
    js_bundle_bytes: int = 0
    css_bundle_bytes: int = 0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "ttfb_ms": self.ttfb_ms,
            "fcp_ms": self.fcp_ms,
            "lcp_ms": self.lcp_ms,
            "dom_interactive_ms": self.dom_interactive_ms,
            "dom_complete_ms": self.dom_complete_ms,
            "full_load_ms": self.full_load_ms,
            "total_requests": self.total_requests,
            "total_transfer_bytes": self.total_transfer_bytes,
            "js_bundle_bytes": self.js_bundle_bytes,
            "css_bundle_bytes": self.css_bundle_bytes,
        }

    def check_regression(
        self, baseline: "PerfMetrics | None"
    ) -> dict[str, str]:
        """对比基线检测回归。

        参考 gstack /benchmark 的阈值：
        - >50% 或 >500ms → REGRESSION
        - >20% → WARNING
        """
        if baseline is None:
            return {"fcp_ms": "BASELINE", "lcp_ms": "BASELINE"}

        results = {}
        timing_metrics = ["fcp_ms", "lcp_ms", "ttfb_ms"]
        for metric in timing_metrics:
            base_val = getattr(baseline, metric, 0)
            curr_val = getattr(self, metric, 0)
            if base_val <= 0:
                results[metric] = "BASELINE"
                continue

            delta_pct = (curr_val - base_val) / base_val * 100
            delta_abs = curr_val - base_val

            if delta_pct > 50 or delta_abs > 500:
                results[metric] = "REGRESSION"
            elif delta_pct > 20:
                results[metric] = "WARNING"
            else:
                results[metric] = "OK"

        return results


def collect_perf_metrics(page: Page) -> PerfMetrics:
    """采集页面 Web Vitals 性能指标。

    参考 gstack /benchmark：使用 performance.getEntriesByType('navigation')。
    """
    metrics = PerfMetrics()

    try:
        nav_timing = page.evaluate("""() => {
            const nav = performance.getEntriesByType('navigation')[0];
            if (!nav) return null;
            return {
                ttfb: nav.responseStart - nav.requestStart,
                fcp: 0,
                lcp: 0,
                domInteractive: nav.domInteractive - nav.startTime,
                domComplete: nav.domComplete - nav.startTime,
                loadComplete: nav.loadEventEnd - nav.startTime,
            };
        }""")

        if nav_timing:
            metrics.ttfb_ms = nav_timing.get("ttfb", 0)
            metrics.dom_interactive_ms = nav_timing.get("domInteractive", 0)
            metrics.dom_complete_ms = nav_timing.get("domComplete", 0)
            metrics.full_load_ms = nav_timing.get("loadComplete", 0)

        # FCP
        fcp = page.evaluate("""() => {
            const entries = performance.getEntriesByType('paint');
            const fcp = entries.find(e => e.name === 'first-contentful-paint');
            return fcp ? fcp.startTime : 0;
        }""")
        metrics.fcp_ms = fcp or 0

        # LCP (需要 PerformanceObserver — 可能不是立即可用)
        lcp = page.evaluate("""() => {
            return new Promise((resolve) => {
                let lcp = 0;
                const observer = new PerformanceObserver((list) => {
                    const entries = list.getEntries();
                    if (entries.length > 0) {
                        lcp = entries[entries.length - 1].startTime;
                    }
                });
                observer.observe({type: 'largest-contentful-paint', buffered: true});
                // 等待一个微任务让 observer 获取 buffered entries
                setTimeout(() => { observer.disconnect(); resolve(lcp); }, 200);
            });
        }""")
        metrics.lcp_ms = lcp or 0

        # 资源统计
        resources = page.evaluate("""() => {
            const entries = performance.getEntriesByType('resource');
            let total = 0, js = 0, css = 0, count = entries.length;
            entries.forEach(e => {
                total += e.transferSize || 0;
                if (e.initiatorType === 'script') js += e.transferSize || 0;
                if (e.initiatorType === 'css' || e.name.endsWith('.css')) css += e.transferSize || 0;
            });
            return {total, js, css, count};
        }""")
        if resources:
            metrics.total_transfer_bytes = resources.get("total", 0)
            metrics.total_requests = resources.get("count", 0)
            metrics.js_bundle_bytes = resources.get("js", 0)
            metrics.css_bundle_bytes = resources.get("css", 0)

    except Exception:
        pass  # 性能采集非关键路径

    return metrics


def save_perf_baseline(metrics: PerfMetrics, filepath: str | Path) -> None:
    """保存性能基线（参考 gstack /benchmark 的 baseline.json）。"""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = metrics.to_dict()
    data["timestamp"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    path.write_text(json.dumps(data, indent=2))


def load_perf_baseline(filepath: str | Path) -> PerfMetrics | None:
    """加载性能基线。"""
    path = Path(filepath)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return PerfMetrics(**{k: v for k, v in data.items() if k in PerfMetrics.__dataclass_fields__})


# ============================================================
# 视觉断言辅助 — 参考 gstack screenshot + AutoCLI image matching
# ============================================================

def take_annotated_screenshot(
    page: Page,
    name: str,
    selector: str | None = None,
    full_page: bool = False,
) -> Path:
    """截取带标注的截图。

    参考 gstack "snapshot -a -o path.png" 的标注模式。
    这里简化实现：截图 + 如果有 selector，在元素周围画红色边框。

    返回截图文件路径。
    """
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{name}_{timestamp}.png"
    filepath = SCREENSHOT_DIR / filename

    if selector:
        try:
            # 在目标元素周围添加高亮边框
            page.evaluate(
                """(sel) => {
                const el = document.querySelector(sel);
                if (el) {
                    el.style.outline = '3px solid red';
                    el.style.outlineOffset = '2px';
                    el.dataset.__e2e_annotated = 'true';
                }
            }""",
                selector,
            )
        except Exception:
            pass

    page.screenshot(path=str(filepath), full_page=full_page)

    # 清理高亮
    if selector:
        try:
            page.evaluate(
                """() => {
                const els = document.querySelectorAll('[data-__e2e_annotated]');
                els.forEach(el => { el.style.outline = ''; el.style.outlineOffset = ''; });
            }"""
            )
        except Exception:
            pass

    return filepath


def assert_visual_match(
    page: Page,
    name: str,
    max_diff_pixel_ratio: float = 0.01,
    full_page: bool = True,
) -> None:
    """视觉回归断言。

    参考 AutoCLI 的图像匹配 threshold 模式：
    - max_diff_pixel_ratio: 允许的像素差异比例（默认 1%）

    使用 Playwright 内置的 toHaveScreenshot() 实现。
    """
    snapshot_path = BASELINE_DIR / f"{name}.png"
    expect(page).to_have_screenshot(
        path=str(snapshot_path),
        full_page=full_page,
        max_diff_pixel_ratio=max_diff_pixel_ratio,
    )


def visual_snapshot(
    page: Page,
    name: str,
    full_page: bool = True,
) -> Path:
    """截取视觉快照并保存到 baselines 目录。

    首次运行时生成基线，后续运行进行对比。
    """
    filepath = BASELINE_DIR / f"{name}.png"
    page.screenshot(path=str(filepath), full_page=full_page)
    print(f"[Visual] 快照已保存: {filepath}")
    return filepath


# ============================================================
# 通用 UI 断言辅助
# ============================================================

def assert_visible(page: Page, selector: str, timeout: int = 5000) -> None:
    """断言元素可见。"""
    expect(page.locator(selector).first).to_be_visible(timeout=timeout)


def assert_hidden(page: Page, selector: str, timeout: int = 5000) -> None:
    """断言元素不可见（不存在于 DOM 或 display:none）。"""
    expect(page.locator(selector).first).to_be_hidden(timeout=timeout)


def assert_text_contains(page: Page, text: str) -> None:
    """断言页面包含指定文本。"""
    expect(page.get_by_text(text).first).to_be_visible()


def assert_has_class(page: Page, selector: str, class_name: str) -> None:
    """断言元素有指定 CSS class。"""
    expect(page.locator(selector).first).to_have_class(
        class_name,
        timeout=5000,
    )


def wait_for_network_idle(page: Page, timeout: int = 10000) -> None:
    """等待网络请求完成（参考 gstack 的 networkidle 等待模式）。"""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass  # 某些单页应用可能永远不会 fully idle


def explore_page(page: Page) -> dict[str, Any]:
    """分析页面结构，输出可交互元素和推荐的选择器。

    参考 AutoCLI (nashsu/AutoCLI) 的 `explore` 命令：
    - 分析页面 API endpoints
    - 发现 Pinia/Vuex stores
    - 提取 __INITIAL_STATE__ (SSR 站点)
    - 自动发现搜索 endpoint

    对 harness 看板的适配：
    - 提取所有 button/a/input 元素及其文本/role
    - dump 可访问性树摘要
    - 列举可见文本块
    - 输出建议的 YAML 场景模板
    """
    result: dict[str, Any] = {
        "url": page.url,
        "title": page.title(),
        "interactive_elements": [],
        "headings": [],
        "text_blocks": [],
        "vue_stores": None,
        "framework": None,
    }

    # 检测框架
    result["framework"] = page.evaluate("""() => {
        if (window.__VUE__ || document.querySelector('[data-v-]')) return 'vue';
        if (window.__NUXT__) return 'nuxt';
        if (document.querySelector('[data-reactroot]')) return 'react';
        return 'unknown';
    }""")

    # 检测 Vuex/Pinia (AutoCLI 的 store discovery 模式)
    result["vue_stores"] = page.evaluate("""() => {
        const stores = [];
        try {
            // Pinia
            const pinia = document.querySelector('[data-v-app]')?.__vue_app__?.config?.globalProperties?.$pinia;
            if (pinia && pinia._s) {
                for (const [name, store] of Object.entries(pinia._s)) {
                    stores.push({type: 'pinia', name: name});
                }
            }
        } catch(e) {}
        return stores.length > 0 ? stores : null;
    }""")

    # 可交互元素（参考 AutoCLI 的 selector 提取）
    interactive = page.evaluate("""() => {
        const items = [];
        const seen = new Set();
        const selectors = 'button, a, input, select, textarea, [role="button"], [role="tab"], [onclick]';
        document.querySelectorAll(selectors).forEach(el => {
            const text = el.textContent?.trim().substring(0, 50) || '';
            const tag = el.tagName.toLowerCase();
            const role = el.getAttribute('role') || '';
            const type = el.getAttribute('type') || '';
            const id = el.id || '';
            const classes = Array.from(el.classList).join(' ').substring(0, 80);
            const key = `${tag}:${text}`;
            if (!seen.has(key) && text) {
                seen.add(key);
                items.push({tag, text, role, type, id, classes});
            }
        });
        return items.slice(0, 50);  // 限制 50 个
    }""")
    result["interactive_elements"] = interactive

    # 标题
    result["headings"] = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('h1, h2, h3')).map(h => ({
            level: h.tagName,
            text: h.textContent?.trim().substring(0, 100),
        }));
    }""")

    # 可见文本块
    result["text_blocks"] = page.evaluate("""() => {
        const blocks = [];
        const walker = document.createTreeWalker(
            document.body,
            NodeFilter.SHOW_TEXT,
            null
        );
        let node;
        while (node = walker.nextNode()) {
            const text = node.textContent?.trim();
            if (text && text.length > 10 && text.length < 200) {
                blocks.push(text);
            }
        }
        return blocks.slice(0, 30);
    }""")

    return result


def print_explore_report(result: dict[str, Any]) -> str:
    """将 explore 结果格式化为可读报告。

    用于辅助编写 YAML 测试场景：了解页面有哪些可交互元素后，
    直接复制文本到 YAML 的 selector 字段。
    """
    lines = [
        f"=== Page Explore Report ===",
        f"URL:       {result['url']}",
        f"Title:     {result['title']}",
        f"Framework: {result.get('framework', 'unknown')}",
        f"",
    ]

    if result.get("vue_stores"):
        lines.append("--- Vue Stores (Pinia) ---")
        for s in result["vue_stores"]:
            lines.append(f"  {s['type']}: {s['name']}")
        lines.append("")

    lines.append("--- Headings ---")
    for h in result.get("headings", []):
        lines.append(f"  {h['level']}: {h['text']}")
    lines.append("")

    lines.append("--- Interactive Elements (top 50) ---")
    for el in result.get("interactive_elements", []):
        sel = f'text={el["text"]}' if el["text"] else el.get("id", el["tag"])
        lines.append(f"  [{el['tag']}] {el['text'][:60]}  →  selector: \"{sel}\"")
    lines.append("")

    lines.append("--- Suggested YAML Steps ---")
    for el in result.get("interactive_elements", [])[:10]:
        if el["tag"] in ("button", "a") and el["text"]:
            lines.append(f"  - action: click")
            lines.append(f'    selector: "text={el["text"]}"')
            lines.append(f"    description: \"点击 {el['text']}\"")
            lines.append("")

    return "\n".join(lines)


def snapshot_diff(before_text: str, after_text: str) -> str:
    """对比两次快照文本的差异（参考 gstack "snapshot -D" 的 diff 模式）。

    返回 unified diff 格式的字符串。
    """
    import difflib

    before_lines = before_text.splitlines(keepends=True)
    after_lines = after_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines, after_lines,
        fromfile="before", tofile="after",
        lineterm="",
    )
    return "\n".join(diff)
