from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "doc-to-deps"
    / "scripts"
    / "doc_to_deps.py"
)
SPEC = importlib.util.spec_from_file_location("doc_to_deps", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_task_splitting_emits_default_acp_agents():
    deps = MODULE.build_deps([
        {"text": "Design architecture", "heading": "Platform", "is_heading": False},
        {"text": "Implement backend API", "heading": "Platform", "is_heading": False},
        {"text": "Review the changes", "heading": "Platform", "is_heading": False},
    ])

    assert deps["design_architecture"]["acp"] == {"agent": "claude"}
    assert deps["implement_backend_api"]["acp"] == {"agent": "codex"}
    assert deps["review_the_changes"]["acp"] == {"agent": "claude"}
    assert all("agent_name" not in task for task in deps.values())


def test_task_specific_acp_override_wins_over_type_default():
    deps = MODULE.build_deps(
        [{"text": "Implement backend API", "heading": "Platform", "is_heading": False}],
        acp_map={"implement_backend_api": "claude"},
    )

    assert deps["implement_backend_api"]["acp"] == {"agent": "claude"}
