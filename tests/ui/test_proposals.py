"""
Proposal 管理 UI 自动化测试 (Playwright)

前置条件：
1. Consul 启动
2. 框架 WebAPI 启动（默认 8080 端口）
3. 依赖安装：pip install playwright && playwright install chromium

运行方式：
pytest tests/ui/test_proposals.py -v
或
python tests/ui/test_proposals.py
"""
from __future__ import annotations

import json
import time
from datetime import datetime

import pytest
from playwright.sync_api import Page, expect


BASE_URL = "http://localhost:8080"
REQ_ID = f"ui-test-{datetime.now().strftime('%Y%m%d%H%M%S')}"


class TestProposalWorkflow:
    """提案管理工作流测试"""

    @pytest.fixture(autouse=True)
    def def setup(self, page: Page):
        """每个测试前准备环境"""
        self.page = page
        self.consul_url = "http://localhost:8500"

    def _setup_requirement(self, status: str = "CONFIRMED"):
        """通过 Consul API 初始化需求数据"""
        import requests

        base = f"{self.consul_url}/v1/kv"

        def put(key: str, value: str):
            requests.put(f"{base}/{key}", data=value.encode())

        put(f"workflows/{REQ_ID}/status", status)
        put(f"workflows/{REQ_ID}/title", "UI自动化测试需求")
        put(
            f"workflows/{REQ_ID}/dependencies",
            json.dumps({
                "design": {"type": "design", "depends_on": []},
                "backend": {"type": "backend", "depends_on": ["design"]},
                "test": {"type": "test", "depends_on": ["backend"]},
            }),
        )
        put(f"workflows/{REQ_ID}/tasks/design/status", "DONE")
        put(f"workflows/{REQ_ID}/tasks/backend/status", "IN_PROGRESS")
        put(f"workflows/{REQ_ID}/tasks/backend/started_at", datetime.utcnow().isoformat() + "Z")

    def _cleanup(self):
        """清理测试数据"""
        import requests
        requests.delete(f"{self.consul_url}/v1/kv/workflows/{REQ_ID}?recurse=true")

    def _get_status(self) -> str:
        """获取需求状态"""
        import requests
        resp = requests.get(f"{self.consul_url}/v1/kv/workflows/{REQ_ID}/status")
        if resp.status_code == 200 and resp.json():
            return resp.json()[0]["Value"]
        return ""

    def _set_proposal(self, task_name: str):
        """设置 Proposal 状态并添加新任务"""
        import requests

        base = f"{self.consul_url}/v1/kv"

        def put(key: str, value: str):
            requests.put(f"{base}/{key}", data=value.encode())

        deps = json.dumps({
            "design": {"type": "design", "depends_on": []},
            "backend": {"type": "backend", "depends_on": ["design"]},
            "test": {"type": "test", "depends_on": ["backend"]},
            task_name: {
                "type": "task",
                "depends_on": ["backend"],
                "proposed_by": "test",
                "proposed_at": datetime.utcnow().isoformat() + "Z",
                "reason": "performance test failed",
            },
        })
        put(f"workflows/{REQ_ID}/dependencies", deps)
        put(f"workflows/{REQ_ID}/status", "Proposal")

    def test_view_workflow_with_proposal(self, page: Page):
        """查看处于 Proposal 状态的需求"""
        self._setup_requirement("CONFIRMED")

        page.goto(f"{BASE_URL}/api/workflows")
        page.wait_for_load_state("networkidle")

        page.goto(f"{BASE_URL}/api/workflow/{REQ_ID}")
        page.wait_for_load_state("networkidle")

        resp = json.loads(page.content())
        assert resp["status"] == "CONFIRMED"
        assert "design" in resp["dependencies"]

        self._cleanup()

    def test_proposal_pending_alert(self, page: Page):
        """当需求处于 Proposal 状态时，看板应显示提示"""
        self._setup_requirement()
        self._set_proposal("perf-opt")

        page.goto(f"{BASE_URL}/api/workflow/{REQ_ID}")
        resp = json.loads(page.content())

        assert resp["status"] == "Proposal"
        proposals = resp.get("proposals", [])
        if not proposals:
            proposals = [t for t in resp.get("dependencies", {}).values()
                        if t.get("proposed_by")]

        assert len(proposals) > 0, "应该显示待确认的提案"

        self._cleanup()

    def test_confirm_proposal_via_api(self, page: Page):
        """通过 API 确认提案"""
        self._setup_requirement()
        self._set_proposal("perf-opt")

        assert self._get_status() == "Proposal"

        page.request.post(
            f"{BASE_URL}/api/workflow/{REQ_ID}/proposals",
            json={"action": "confirm"},
        )

        time.sleep(0.5)
        assert self._get_status() == "CONFIRMED"

        self._cleanup()

    def test_reject_proposal_via_api(self, page: Page):
        """通过 API 拒绝提案"""
        self._setup_requirement()
        self._set_proposal("perf-opt")

        assert self._get_status() == "Proposal"

        page.request.post(
            f"{BASE_URL}/api/workflow/{REQ_ID}/proposals",
            json={"action": "reject"},
        )

        time.sleep(0.5)
        assert self._get_status() == "CONFIRMED"

        deps_resp = page.request.get(f"{BASE_URL}/api/workflow/{REQ_ID}")
        deps = json.loads(deps_resp.text).get("dependencies", {})
        assert "perf-opt" not in deps, "被拒绝的任务应该从 dependencies 中移除"

        self._cleanup()

    def test_confirm_with_partial_rejection(self, page: Page):
        """确认时部分拒绝（接受部分新任务）"""
        self._setup_requirement()
        self._set_proposal("perf-opt")

        page.request.post(
            f"{BASE_URL}/api/workflow/{REQ_ID}/proposals",
            json={"action": "confirm", "rejected_tasks": ["perf-opt"]},
        )

        time.sleep(0.5)
        assert self._get_status() == "CONFIRMED"

        self._cleanup()

    def test_proposals_list(self, page: Page):
        """获取提案列表"""
        self._setup_requirement()
        self._set_proposal("perf-opt")

        resp = page.request.get(f"{BASE_URL}/api/workflow/{REQ_ID}/proposals")
        data = json.loads(resp.text)

        assert data["status"] == "Proposal"
        assert len(data["proposals"]) >= 1

        perf_opt = next((p for p in data["proposals"] if p["task_name"] == "perf-opt"), None)
        assert perf_opt is not None
        assert perf_opt["proposed_by"] == "test"

        self._cleanup()

    def test_confirm_proposal_when_not_in_proposal_state(self, page: Page):
        """非 Proposal 状态确认失败"""
        self._setup_requirement("CONFIRMED")

        resp = page.request.post(
            f"{BASE_URL}/api/workflow/{REQ_ID}/proposals",
            json={"action": "confirm"},
        )

        assert resp.status_code == 400

        self._cleanup()


class TestProposalVisibility:
    """提案可见性测试"""

    def test_proposals_in_workflow_list(self, page: Page):
        """工作流列表中标识 Proposal 状态的需求"""
        import requests

        req_id = f"visibility-test-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        requests.put(
            f"http://localhost:8500/v1/kv/workflows/{req_id}/status",
            data="Proposal".encode(),
        )
        requests.put(
            f"http://localhost:8500/v1/kv/workflows/{req_id}/title",
            data="测试Proposal".encode(),
        )
        requests.put(
            f"http://localhost:8500/v1/kv/workflows/{req_id}/dependencies",
            data=json.dumps({}).encode(),
        )

        page.goto(f"{BASE_URL}/api/workflows")
        resp = json.loads(page.content())

        wf = next((w for w in resp.get("workflows", []) if w["req_id"] == req_id), None)
        assert wf is not None

        requests.delete(f"http://localhost:8500/v1/kv/workflows/{req_id}?recurse=true")


class TestProposalAgentIntegration:
    """Agent 集成测试（模拟 Agent 提出提案）"""

    def test_agent_proposes_task(self, page: Page):
        """模拟 Agent 提出新任务"""
        import requests

        req_id = f"agent-test-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        requests.put(f"http://localhost:8500/v1/kv/workflows/{req_id}/status", data="IN_PROGRESS".encode())
        requests.put(f"http://localhost:8500/v1/kv/workflows/{req_id}/title", data="Agent测试".encode())
        requests.put(
            f"http://localhost:8500/v1/kv/workflows/{req_id}/dependencies",
            data=json.dumps({"design": {"type": "design"}}).encode(),
        )

        deps_resp = requests.get(f"http://localhost:8500/v1/kv/workflows/{req_id}/dependencies")
        deps = json.loads(deps_resp.json()[0]["Value"])
        deps["sec-fix"] = {
            "type": "task",
            "depends_on": ["design"],
            "proposed_by": "backend",
            "proposed_at": datetime.utcnow().isoformat() + "Z",
        }
        requests.put(
            f"http://localhost:8500/v1/kv/workflows/{req_id}/dependencies",
            data=json.dumps(deps).encode(),
        )

        idx_resp = requests.get(f"http://localhost:8500/v1/kv/workflows/{req_id}/status")
        idx = idx_resp.json()[0]["ModifyIndex"]
        requests.put(
            f"http://localhost:8500/v1/kv/workflows/{req_id}/status",
            data="Proposal".encode(),
            params={"cas": idx},
        )

        resp = page.request.get(f"{BASE_URL}/api/workflow/{req_id}/proposals")
        data = json.loads(resp.text)

        assert data["status"] == "Proposal"
        sec_fix = next((p for p in data["proposals"] if p["task_name"] == "sec-fix"), None)
        assert sec_fix is not None
        assert sec_fix["proposed_by"] == "backend"

        requests.delete(f"http://localhost:8500/v1/kv/workflows/{req_id}?recurse=true")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])