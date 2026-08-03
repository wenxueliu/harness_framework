from __future__ import annotations

from scripts.change_requirement import parse_tasks


def test_parse_tasks_normalizes_csv():
    assert parse_tasks(" api, test ,,api ") == ["api", "test", "api"]
    assert parse_tasks("") == []
