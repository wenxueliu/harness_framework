"""Kimi WebBridge fixture for UI API tests."""
from __future__ import annotations

import pytest

from tests.e2e.webbridge import Page


@pytest.fixture
def page(request: pytest.FixtureRequest) -> Page:
    if not Page.available():
        pytest.skip("Kimi WebBridge daemon unavailable at 127.0.0.1:10086")
    return Page(session=f"harness-ui-{request.node.name}")
