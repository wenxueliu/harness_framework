"""UI 用例：作业流任务的人工消息面板。

回归背景：t2 实例的 dev 任务里输入 hi 点发送没有任何反应——后端 404
（只认旧 workflow 布局），而前端只在实例加载失败时才显示错误。
本用例覆盖：发送成功后有可见反馈、失败时有可见错误。
"""
from __future__ import annotations

import os
import time
import uuid

import requests

from tests.e2e.conftest import WEBAPI_URL, consul_delete
from tests.e2e.webbridge import Page

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:3000")
TASK_ID = "dev"


def _api(method: str, path: str, body: dict | None = None) -> dict:
    response = requests.request(
        method, f"{WEBAPI_URL}{path}",
        json=body, timeout=10,
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    response.raise_for_status()
    return response.json()


def _create_instance() -> str:
    """建一个未启动的实例：任务停在 PENDING，不会真的拉起 Agent。"""
    name = f"ui-msg-{time.time_ns()}"
    group = _api("POST", "/api/project-groups", {"name": name, "description": "ui-msg"})
    template = _api("POST", "/api/templates", {
        "name": f"{name}-tpl", "description": "ui-msg",
        "group_id": group["project_group"]["group_id"],
        "tasks": [{"id": TASK_ID, "name": "开发", "type": "backend",
                   "agent": "codex", "depends_on": []}],
    })
    template_id = template["template"]["template_id"]
    version = _api("POST", f"/api/templates/{template_id}/publish", {})
    instance = _api("POST", "/api/instances", {
        "template_id": template_id,
        "version_id": version["version"]["version_id"],
        "name": name, "parameters": {},
        "workspace": {"strategy": "DEMO_TEMP"},
    })
    return instance["instance"]["instance_id"], template_id, group["project_group"]["group_id"]


def _cleanup(instance_id: str, template_id: str, group_id: str) -> None:
    for prefix in (f"jobflows/instances/{instance_id}",
                   f"jobflows/templates/{template_id}",
                   f"project-groups/{group_id}"):
        try:
            consul_delete(prefix, recurse=True)
        except Exception:
            pass


def _open_task(page: Page, instance_id: str) -> None:
    page.goto(f"{BASE_URL}/#/instances/{instance_id}/tasks/{TASK_ID}")
    page.wait_for_selector('[data-testid="human-message-input"]', timeout=15000)


class TestJobFlowTaskHumanMessage:
    def test_send_message_is_recorded_and_visible(self, page: Page):
        instance_id, template_id, group_id = _create_instance()
        try:
            _open_task(page, instance_id)
            assert "已记录 0 条人工消息" in page.content()

            page.fill('[data-testid="human-message-input"]', "hi")
            page.click('[data-testid="send-human-message"]')
            page.wait_for_timeout(1500)

            content = page.content()
            assert page.query_selector('[data-testid="task-action-error"]') is None, content
            assert "已记录 1 条人工消息" in content
            assert page.query_selector('[data-testid="human-message-list"]') is not None
            assert "hi" in page.inner_text('[data-testid="human-message-list"]')

            listed = _api("GET", f"/api/workflow/{instance_id}/task/{TASK_ID}/messages")
            assert [item["message"] for item in listed["messages"]] == ["hi"]
        finally:
            _cleanup(instance_id, template_id, group_id)

    def test_send_failure_is_surfaced_to_the_user(self, page: Page):
        """实例加载成功但发送失败时，错误必须可见（此前被静默吞掉）。"""
        instance_id, template_id, group_id = _create_instance()
        try:
            _open_task(page, instance_id)
            # 让发送请求返回 404，模拟「消息写不进去」的失败路径
            page.evaluate("""() => {
              const original = window.fetch;
              window.fetch = (input, init) => {
                const url = typeof input === 'string' ? input : input.url;
                if (url.includes('/messages') && init && init.method === 'POST') {
                  return Promise.resolve(new Response(
                    JSON.stringify({error: 'task not found'}),
                    {status: 404, headers: {'Content-Type': 'application/json'}}));
                }
                return original(input, init);
              };
              return true;
            }""")
            page.fill('[data-testid="human-message-input"]', "hi")
            page.click('[data-testid="send-human-message"]')
            page.wait_for_selector('[data-testid="task-action-error"]', timeout=10000)
            assert "task not found" in page.inner_text('[data-testid="task-action-error"]')
            assert "已记录 0 条人工消息" in page.content()
        finally:
            _cleanup(instance_id, template_id, group_id)
