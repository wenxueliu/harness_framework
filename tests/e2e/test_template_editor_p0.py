"""P0 template editor contracts: schema forms, DAG errors, and optimistic locking."""
from __future__ import annotations

import json
import time

import pytest

from .conftest import api_json
from .helpers import wait_for_network_idle
from .webbridge import Page, expect


def _editor_payload(page: Page, key: str, value: object) -> None:
    """Set Monaco values through its native edit context."""
    text = json.dumps(value, ensure_ascii=False, indent=2)
    selector = (
        '[data-testid="tasks-editor"] .native-edit-context' if key == 'tasks'
        else '[data-testid="schema-editor"] .native-edit-context'
    )
    page.locator(selector).wait_for("visible", timeout=10000)
    page.locator(selector).click()
    inserted = page.evaluate("""(value) => {
      if (document.activeElement?.getAttribute('contenteditable') !== 'true') return false;
      document.execCommand('selectAll');
      return document.execCommand('insertText', false, value);
    }""", text)
    assert inserted is True


@pytest.mark.e2e
def test_template_editor_publishes_valid_schema(
    page: Page, dashboard_url: str,
) -> None:
    suffix = str(time.time_ns())
    template_id = f"ui-editor-valid-{suffix}"
    page.goto(dashboard_url + "/#/templates/new")
    wait_for_network_idle(page)
    page.wait_for_selector('[data-testid="template-name"]')
    page.fill('[data-testid="template-name"]', 'UI valid publish ' + suffix)
    _editor_payload(page, 'tasks', [
        {"id": "build", "type": "backend", "agent": "claude", "depends_on": []},
        {"id": "test", "type": "test", "agent": "codex", "depends_on": ["build"]},
    ])
    _editor_payload(page, 'schema', {
        "type": "object",
        "properties": {"region": {"type": "string"}},
        "required": ["region"],
    })
    page.wait_for_selector('[data-testid="parameter-region"]', timeout=10000)
    page.fill('[data-testid="parameter-region"]', 'cn')
    page.click('[data-testid="save-draft"]')
    expect(page.get_by_text('草稿已保存')).to_be_visible(timeout=15000)
    template_id = str(page.evaluate(
        "() => location.hash.split('/templates/')[1]?.split('/')[0] || ''"
    ))
    assert template_id
    try:
        template = api_json('GET', '/api/templates/' + template_id)
        assert template['template']['draft']['parameter_schema']['required'] == ['region']
        assert len(template['template']['draft']['tasks']) == 2
    finally:
        try:
            api_json('DELETE', '/api/templates/' + template_id)
        except RuntimeError:
            pass


@pytest.mark.e2e
def test_template_editor_blocks_cycle_with_location(
    page: Page, dashboard_url: str,
) -> None:
    page.goto(dashboard_url + '/#/templates/new')
    wait_for_network_idle(page)
    page.wait_for_selector('[data-testid="template-name"]')
    _editor_payload(page, 'tasks', [
        {"id": "build", "type": "backend", "agent": "claude", "depends_on": ["test"]},
        {"id": "test", "type": "test", "agent": "codex", "depends_on": ["build"]},
    ])
    page.click('[data-testid="publish-only"]')
    expect(page.get_by_text('作业流依赖不能形成环')).to_be_visible(timeout=15000)
    body = page.content()
    assert '边 build->test' in body or '边 test->build' in body
    assert '第 ' in body


@pytest.mark.e2e
def test_template_editor_shows_revision_conflict(
    page: Page, dashboard_url: str,
) -> None:
    suffix = str(time.time_ns())
    template_id = f"ui-editor-conflict-{suffix}"
    api_json('POST', '/api/templates', {
        'template_id': template_id,
        'name': 'UI conflict ' + suffix,
        'tasks': [
            {'id': 'build', 'type': 'backend', 'agent': 'claude', 'depends_on': []},
            {'id': 'test', 'type': 'test', 'agent': 'codex', 'depends_on': ['build']},
        ],
    })
    page.goto(dashboard_url + f'/#/templates/{template_id}/edit')
    wait_for_network_idle(page)
    expect(page.get_by_text('2 个节点')).to_be_visible(timeout=10000)
    api_json('PATCH', f'/api/templates/{template_id}/draft', {
        'expected_revision': 1,
        'description': 'Changed by another editor',
        'tasks': [
            {'id': 'build', 'type': 'backend', 'agent': 'claude', 'depends_on': []},
            {'id': 'test', 'type': 'test', 'agent': 'codex', 'depends_on': ['build']},
        ],
    })
    page.fill('[data-testid="template-name"]', 'UI conflict changed ' + suffix)
    page.click('[data-testid="save-draft"]')
    expect(page.locator('[data-testid="revision-conflict"]')).to_be_visible(timeout=15000)
    expect(page.get_by_text('草稿已被他人修改，请刷新后重试')).to_be_visible(timeout=5000)
    page.click('[data-testid="conflict-refresh"]')
    expect(page.locator('[data-testid="revision-conflict"]')).to_be_hidden(timeout=15000)
    try:
        api_json('DELETE', f'/api/templates/{template_id}')
    except RuntimeError:
        pass
