"""
sync_to_consul 单元测试

用例:
- validate valid/invalid dependencies.json
- write_workflow 写入 Consul KV 结构
- 叶子任务初始 PENDING，非叶子初始 BLOCKED
- blocking=false 任务初始 PENDING（即使有依赖）
- parallel/aggregate 节点初始 BLOCKED
- metadata 字段正确透传
- 幂等：workflow_exists 检测
- --force 覆盖
"""
from __future__ import annotations

import json
import sys
import os
from unittest.mock import MagicMock, Mock

import pytest

# 确保能导入 scripts/sync_to_consul
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from scripts.sync_to_consul import (
    validate_dependencies,
    write_workflow,
    workflow_exists,
)


class TestValidateDependencies:
    def test_valid_minimal(self):
        """最小合法格式。"""
        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "design", "type": "design", "depends_on": []},
            ],
        }
        assert validate_dependencies(data) == []

    def test_valid_with_waves(self):
        """带 wave (parallel/aggregate) 的完整格式。"""
        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "wave-1", "type": "parallel", "depends_on": [],
                 "children": ["hw-001"]},
                {"name": "hw-001", "type": "task", "service_name": "user-service",
                 "depends_on": []},
                {"name": "wave-1-merge", "type": "aggregate",
                 "depends_on": ["wave-1"]},
            ],
        }
        assert validate_dependencies(data) == []

    def test_missing_tasks(self):
        """缺少 tasks 数组。"""
        data = {"req_id": "req-001"}
        errors = validate_dependencies(data)
        assert len(errors) > 0

    def test_empty_tasks(self):
        """tasks 为空数组。"""
        data = {"req_id": "req-001", "tasks": []}
        errors = validate_dependencies(data)
        assert len(errors) > 0

    def test_missing_name(self):
        """任务缺少 name。"""
        data = {
            "req_id": "req-001",
            "tasks": [{"type": "task"}],
        }
        errors = validate_dependencies(data)
        assert any("missing 'name'" in e for e in errors)

    def test_duplicate_name(self):
        """重复的任务名。"""
        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "backend", "type": "task", "depends_on": []},
                {"name": "backend", "type": "task", "depends_on": []},
            ],
        }
        errors = validate_dependencies(data)
        assert any("duplicate name" in e for e in errors)

    def test_invalid_type(self):
        """非法任务类型。"""
        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "bad", "type": "invalid_type", "depends_on": []},
            ],
        }
        errors = validate_dependencies(data)
        assert any("invalid type" in e for e in errors)

    def test_parallel_missing_children(self):
        """parallel 节点缺少 children。"""
        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "wave-1", "type": "parallel", "depends_on": []},
            ],
        }
        errors = validate_dependencies(data)
        assert any("missing 'children'" in e for e in errors)

    def test_depends_on_unknown_task(self):
        """depends_on 引用了不存在的任务。"""
        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "backend", "type": "task",
                 "depends_on": ["nonexistent"]},
            ],
        }
        errors = validate_dependencies(data)
        assert any("not found in tasks" in e for e in errors)


class TestWriteWorkflow:
    def test_leaf_tasks_pending(self):
        """叶子任务（无依赖）初始 PENDING。"""
        consul = MagicMock()
        consul.kv_put = Mock()
        consul.kv_get = Mock(return_value=(None, 0))

        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "design", "type": "design", "depends_on": []},
                {"name": "backend", "type": "backend",
                 "depends_on": ["design"]},
            ],
        }
        result = write_workflow(consul, "req-001", data)

        assert result["ok"]
        assert result["task_count"] == 2

        # 检查 design（叶子）→ PENDING
        design_calls = [
            c for c in consul.kv_put.call_args_list
            if "tasks/design/status" in str(c)
        ]
        assert len(design_calls) == 1
        assert design_calls[0][0][1] == "PENDING"

        # 检查 backend（有依赖）→ BLOCKED
        backend_calls = [
            c for c in consul.kv_put.call_args_list
            if "tasks/backend/status" in str(c)
        ]
        assert len(backend_calls) == 1
        assert backend_calls[0][0][1] == "BLOCKED"

    def test_non_blocking_initial_pending(self):
        """blocking=false 的任务即使有依赖也初始 PENDING。"""
        consul = MagicMock()
        consul.kv_put = Mock()
        consul.kv_get = Mock(return_value=(None, 0))

        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "design", "type": "design", "depends_on": []},
                {"name": "backend", "type": "backend",
                 "depends_on": ["design"], "blocking": False},
            ],
        }
        write_workflow(consul, "req-001", data)

        backend_calls = [
            c for c in consul.kv_put.call_args_list
            if "tasks/backend/status" in str(c)
        ]
        assert len(backend_calls) == 1
        assert backend_calls[0][0][1] == "PENDING", (
            "blocking=false task should start as PENDING"
        )

    def test_parallel_aggregate_initial_blocked(self):
        """parallel/aggregate 节点初始 BLOCKED。"""
        consul = MagicMock()
        consul.kv_put = Mock()
        consul.kv_get = Mock(return_value=(None, 0))

        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "wave-1", "type": "parallel", "depends_on": [],
                 "children": ["hw-001"]},
                {"name": "hw-001", "type": "task", "depends_on": []},
                {"name": "wave-1-merge", "type": "aggregate",
                 "depends_on": ["wave-1"]},
            ],
        }
        write_workflow(consul, "req-001", data)

        parallel_calls = [
            c for c in consul.kv_put.call_args_list
            if "tasks/wave-1/status" in str(c)
        ]
        assert len(parallel_calls) == 1
        assert parallel_calls[0][0][1] == "BLOCKED"

        aggregate_calls = [
            c for c in consul.kv_put.call_args_list
            if "tasks/wave-1-merge/status" in str(c)
        ]
        assert len(aggregate_calls) == 1
        assert aggregate_calls[0][0][1] == "BLOCKED"

    def test_metadata_passthrough(self):
        """metadata 字段正确透传到 Consul KV。"""
        consul = MagicMock()
        consul.kv_put = Mock()
        consul.kv_get = Mock(return_value=(None, 0))

        metadata = {
            "test_bindings": {"ut_cases": ["UT-1"], "api_cases": ["API-1"]},
            "review_requirements": [{"reviewer": "security"}],
        }
        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "hw-001", "type": "task", "depends_on": [],
                 "metadata": metadata},
            ],
        }
        write_workflow(consul, "req-001", data)

        meta_calls = [
            c for c in consul.kv_put.call_args_list
            if "tasks/hw-001/metadata" in str(c)
        ]
        assert len(meta_calls) == 1
        stored = json.loads(meta_calls[0][0][1])
        assert stored == metadata

    def test_dependencies_written(self):
        """dependencies JSON 写入 Consul。"""
        consul = MagicMock()
        consul.kv_put = Mock()
        consul.kv_get = Mock(return_value=(None, 0))

        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "design", "type": "design", "depends_on": []},
                {"name": "backend", "type": "backend",
                 "depends_on": ["design"]},
            ],
        }
        write_workflow(consul, "req-001", data)

        deps_calls = [
            c for c in consul.kv_put.call_args_list
            if "workflows/req-001/dependencies" in str(c)
        ]
        assert len(deps_calls) == 1
        deps_dict = json.loads(deps_calls[0][0][1])
        assert "design" in deps_dict
        assert "backend" in deps_dict
        assert deps_dict["backend"]["depends_on"] == ["design"]

    def test_published_default_false(self):
        """默认 published=false（草稿模式）。"""
        consul = MagicMock()
        consul.kv_put = Mock()
        consul.kv_get = Mock(return_value=(None, 0))

        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "design", "type": "design", "depends_on": []},
            ],
        }
        write_workflow(consul, "req-001", data)

        pub_calls = [
            c for c in consul.kv_put.call_args_list
            if "workflows/req-001/published" in str(c)
        ]
        assert len(pub_calls) == 1
        assert pub_calls[0][0][1] == "false"

    def test_publish_true(self):
        """publish=True 时 published=true。"""
        consul = MagicMock()
        consul.kv_put = Mock()
        consul.kv_get = Mock(return_value=(None, 0))

        data = {
            "req_id": "req-001",
            "tasks": [
                {"name": "design", "type": "design", "depends_on": []},
            ],
        }
        write_workflow(consul, "req-001", data, publish=True)

        pub_calls = [
            c for c in consul.kv_put.call_args_list
            if "workflows/req-001/published" in str(c)
        ]
        assert len(pub_calls) == 1
        assert pub_calls[0][0][1] == "true"


class TestWorkflowExists:
    def test_exists(self):
        """workflow 已存在时返回 True。"""
        consul = MagicMock()
        consul.kv_get = Mock(return_value=('{"design": {}}', 1))
        assert workflow_exists(consul, "req-001") is True

    def test_not_exists(self):
        """workflow 不存在时返回 False。"""
        consul = MagicMock()
        consul.kv_get = Mock(return_value=(None, 0))
        assert workflow_exists(consul, "req-001") is False
