"""Verdict parsing for check-style tasks.

A check task asks the agent to end with a single machine-readable line:

    VERDICT: PASS
    VERDICT: FAIL <reason>

Anything else counts as a failure, so a check node can never silently pass.
"""
from __future__ import annotations

import re

_VERDICT = re.compile(r"VERDICT:\s*(PASS|FAIL)\s*(.*)", re.IGNORECASE)


def parse_verdict(text: str) -> tuple[bool, str]:
    """Return ``(passed, reason)`` for the last verdict found in ``text``."""
    matches = _VERDICT.findall(text or "")
    if not matches:
        return False, "验证者没有给出 VERDICT 结论"
    verdict, reason = matches[-1]
    if verdict.upper() == "PASS":
        return True, ""
    return False, reason.strip() or "验证未通过"
