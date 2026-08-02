from __future__ import annotations

from unittest.mock import Mock

from tests.e2e.webbridge import Page, expect


def _response(payload: dict) -> Mock:
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = payload
    return response


def test_page_navigates_with_stable_session(monkeypatch):
    post = Mock(return_value=_response({"success": True, "url": "http://app"}))
    monkeypatch.setattr("tests.e2e.webbridge.requests.post", post)
    page = Page(session="test-session")
    page.goto("http://app")
    body = post.call_args.kwargs["json"]
    assert body["session"] == "test-session"
    assert body["action"] == "navigate"
    assert body["args"]["group_title"].startswith("Harness E2E")


def test_evaluate_unwraps_webbridge_value(monkeypatch):
    post = Mock(return_value=_response({"success": True, "value": 3}))
    monkeypatch.setattr("tests.e2e.webbridge.requests.post", post)
    assert Page("test").evaluate("() => 3") == 3


def test_locator_visibility_uses_browser_evaluation(monkeypatch):
    post = Mock(return_value=_response({"success": True, "value": True}))
    monkeypatch.setattr("tests.e2e.webbridge.requests.post", post)
    locator = Page("test").locator("#ready")
    expect(locator).to_be_visible(timeout=0)
