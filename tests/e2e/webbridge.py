"""Small synchronous test adapter for the Kimi WebBridge daemon.

It intentionally implements only the Page/Locator surface used by this suite.
Browser operations are sent to the user's real Chrome through WebBridge; there
is no bundled browser dependency.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests


WEBBRIDGE_URL = os.environ.get(
    "KIMI_WEBBRIDGE_URL", "http://127.0.0.1:10086/command"
)


class WebBridgeError(RuntimeError):
    pass


@dataclass
class APIResponse:
    status_code: int
    text: str


class APIRequest:
    def get(self, url: str) -> APIResponse:
        response = requests.get(url, timeout=30)
        return APIResponse(response.status_code, response.text)

    def post(self, url: str, json: dict | None = None) -> APIResponse:
        response = requests.post(url, json=json, timeout=30)
        return APIResponse(response.status_code, response.text)


class Keyboard:
    def __init__(self, page: "Page") -> None:
        self.page = page

    def press(self, key: str) -> None:
        escaped = json.dumps(key)
        self.page.evaluate(
            f"() => document.activeElement?.dispatchEvent(new KeyboardEvent("
            f"'keydown', {{key:{escaped}, bubbles:true}}))"
        )


class Accessibility:
    def __init__(self, page: "Page") -> None:
        self.page = page

    def snapshot(self) -> dict | None:
        return self.page.evaluate("""() => {
          const roleOf = (el) => el.getAttribute('role') || ({
            A:'link',BUTTON:'button',INPUT:'textbox',SELECT:'combobox',
            TEXTAREA:'textbox',H1:'heading',H2:'heading',H3:'heading',
            H4:'heading',H5:'heading',H6:'heading'
          }[el.tagName] || 'generic');
          const walk = (el) => {
            const role = roleOf(el);
            const node = {role, name:(el.getAttribute('aria-label') ||
              el.textContent || el.getAttribute('placeholder') || '').trim()};
            if (/^H[1-6]$/.test(el.tagName)) node.level = Number(el.tagName[1]);
            const children = Array.from(el.children).map(walk);
            if (children.length) node.children = children;
            return node;
          };
          return document.body ? walk(document.body) : null;
        }""")


class Locator:
    def __init__(self, page: "Page", selector: str, *, text: bool = False) -> None:
        self.page = page
        self.selector = selector
        self.text = text

    @property
    def first(self) -> "Locator":
        return self

    def _css(self) -> str:
        if not self.text:
            return self.selector
        token = "kimi-" + uuid.uuid4().hex
        code = """(args) => {
          const visible = el => !!(el.offsetWidth || el.offsetHeight ||
            el.getClientRects().length);
          const match = Array.from(document.querySelectorAll('body *')).find(
            el => visible(el) && (el.textContent || '').includes(args.text) &&
              !Array.from(el.children).some(c => (c.textContent || '').includes(args.text))
          );
          if (!match) return false;
          match.setAttribute('data-kimi-e2e', args.token); return true;
        }"""
        if not self.page.evaluate(code, {"text": self.selector, "token": token}):
            return f'[data-kimi-e2e="{token}"]'
        return f'[data-kimi-e2e="{token}"]'

    def locator(self, selector: str) -> "Locator":
        parent = self._css()
        if selector == "..":
            token = "kimi-" + uuid.uuid4().hex
            self.page.evaluate(
                "(a)=>{const e=document.querySelector(a.sel)?.parentElement;"
                "if(e)e.setAttribute('data-kimi-e2e',a.token)}",
                {"sel": parent, "token": token},
            )
            return Locator(self.page, f'[data-kimi-e2e="{token}"]')
        return Locator(self.page, f"{parent} {selector}")

    def click(self) -> None:
        self.page._command("click", {"selector": self._css()})

    def fill(self, value: str) -> None:
        self.page._command("fill", {"selector": self._css(), "value": value})

    def count(self) -> int:
        return int(self.page.evaluate(
            "s => document.querySelectorAll(s).length", self._css()
        ))

    def is_visible(self, timeout: int = 0) -> bool:
        return self._wait_visibility(True, timeout)

    def is_hidden(self, timeout: int = 0) -> bool:
        return self._wait_visibility(False, timeout)

    def _wait_visibility(self, expected: bool, timeout: int) -> bool:
        deadline = time.time() + timeout / 1000 if timeout else time.time()
        while True:
            visible = bool(self.page.evaluate("""s => {
              const e=document.querySelector(s); if(!e)return false;
              const c=getComputedStyle(e); return c.display!=='none' &&
                c.visibility!=='hidden' && !!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
            }""", self._css()))
            if visible == expected:
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.1)

    def wait_for(self, state: str = "visible", timeout: int = 5000) -> None:
        ok = self.is_visible(timeout) if state == "visible" else self.is_hidden(timeout)
        if not ok:
            raise AssertionError(f"locator {self.selector!r} did not become {state}")

    def scroll_into_view_if_needed(self) -> None:
        self.page.evaluate("s => document.querySelector(s)?.scrollIntoView()", self._css())


class Page:
    def __init__(self, session: str | None = None) -> None:
        self.session = session or f"harness-e2e-{uuid.uuid4().hex[:10]}"
        self.default_timeout = 30_000
        self.request = APIRequest()
        self.keyboard = Keyboard(self)
        self.accessibility = Accessibility(self)
        self._url = ""
        self._console_handlers: list[Callable] = []

    def _command(self, action: str, args: dict | None = None) -> dict:
        response = requests.post(
            WEBBRIDGE_URL,
            json={"action": action, "args": args or {}, "session": self.session},
            timeout=max(30, self.default_timeout / 1000),
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("success") is False or payload.get("error"):
            raise WebBridgeError(str(payload.get("error") or payload))
        return payload

    @classmethod
    def available(cls) -> bool:
        try:
            response = requests.get(WEBBRIDGE_URL.rsplit("/command", 1)[0], timeout=1)
            return response.status_code < 500
        except requests.RequestException:
            return False

    def set_default_timeout(self, timeout: int) -> None:
        self.default_timeout = timeout

    def goto(self, url: str) -> None:
        result = self._command("navigate", {
            "url": url, "newTab": not bool(self._url),
            "group_title": "Harness E2E（Kimi WebBridge）",
        })
        self._url = str(result.get("url", url))

    @property
    def url(self) -> str:
        return self._url

    def reload(self) -> None:
        self.goto(self._url)

    def title(self) -> str:
        return str(self.evaluate("() => document.title"))

    def content(self) -> str:
        return str(self.evaluate("() => document.body?.innerText || ''"))

    def locator(self, selector: str) -> Locator:
        return Locator(self, selector)

    def get_by_text(self, text: str) -> Locator:
        return Locator(self, text, text=True)

    def get_byText(self, text: str) -> Locator:  # legacy typo compatibility
        return self.get_by_text(text)

    def evaluate(self, code: str, *args: Any) -> Any:
        if args:
            argument = json.dumps(args[0], ensure_ascii=False)
            code = f"async () => await (({code}))({argument})"
        result = self._command("evaluate", {"code": code})
        return result.get("value")

    def wait_for_timeout(self, milliseconds: int) -> None:
        time.sleep(milliseconds / 1000)

    def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
        deadline = time.time() + (timeout or self.default_timeout) / 1000
        while time.time() < deadline:
            if self.evaluate("() => document.readyState") == "complete":
                return
            time.sleep(0.1)
        raise TimeoutError(f"page did not reach {state}")

    def set_viewport_size(self, size: dict[str, int]) -> None:
        self._command("cdp", {
            "method": "Emulation.setDeviceMetricsOverride",
            "params": {
                "width": size["width"], "height": size["height"],
                "deviceScaleFactor": 1, "mobile": size["width"] < 600,
            },
        })

    def screenshot(self, path: str, full_page: bool = False) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._command("screenshot", {"format": "png", "path": str(path)})

    def on(self, event: str, handler: Callable) -> None:
        if event == "console":
            self._console_handlers.append(handler)


class Expect:
    def __init__(self, target: Page | Locator) -> None:
        self.target = target

    def to_be_visible(self, timeout: int = 5000) -> None:
        if not isinstance(self.target, Locator) or not self.target.is_visible(timeout):
            raise AssertionError("expected locator to be visible")

    def to_be_hidden(self, timeout: int = 5000) -> None:
        if not isinstance(self.target, Locator) or not self.target.is_hidden(timeout):
            raise AssertionError("expected locator to be hidden")

    def to_have_class(self, class_name: str, timeout: int = 5000) -> None:
        if not isinstance(self.target, Locator):
            raise AssertionError("class assertion requires locator")
        actual = self.target.page.evaluate(
            "s => document.querySelector(s)?.className || ''", self.target._css()
        )
        if class_name not in str(actual):
            raise AssertionError(f"expected class {class_name!r}, got {actual!r}")

    def to_have_screenshot(self, path: str, **_: Any) -> None:
        if isinstance(self.target, Page):
            self.target.screenshot(path)
        else:
            self.target.page._command("screenshot", {
                "format": "png", "path": str(path),
                "selector": self.target._css(),
            })


def expect(target: Page | Locator) -> Expect:
    return Expect(target)
