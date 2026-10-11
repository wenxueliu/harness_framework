"""Kimi WebBridge fixture for UI API tests."""
from __future__ import annotations

import pytest

from tests.e2e.webbridge import Page


@pytest.fixture
def page(request: pytest.FixtureRequest) -> Page:
    if not Page.available():
        pytest.skip("Kimi WebBridge daemon unavailable at 127.0.0.1:10086")
    page = Page(session=f"harness-ui-{request.node.name}")
    yield page
    # Close the tab opened for this test so Chrome does not accumulate tabs
    # and slow down over a long run.
    try:
        page.close_session()
    except Exception:
        pass
