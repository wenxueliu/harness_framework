from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "skills" / "stage-bridge" / "scripts" / "adaptive_boundary.py"
)
SPEC = importlib.util.spec_from_file_location("adaptive_boundary", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_boundary_exit_codes_enforce_control_priority():
    assert MODULE.boundary_exit_code({"blocked": False, "kind": "ACTIVE"}) == 0
    assert MODULE.boundary_exit_code({"blocked": True, "kind": "FEEDBACK"}) == 6
    assert MODULE.boundary_exit_code({"blocked": True, "kind": "ABORT"}) == 7


def test_control_operations_can_cross_blocked_boundary():
    assert MODULE.boundary_exit_code(
        {"blocked": True, "kind": "PAUSE"}, control_operation=True,
    ) == 0
