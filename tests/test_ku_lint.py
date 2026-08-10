from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from omodul.ku_health import list_open_issues
from omodul.ku_lint import KuLintConfig, KuLintInput, ku_lint


def ku(ku_id, **extra):
    value = {"ku_id": ku_id, "knowledge_type": "factual", "grade": "verified", "substrate": ["s1"]}
    value.update(extra)
    return value


def test_each_rule_and_good_data(tmp_path):
    now = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    units = [
        ku("orphan", substrate=[]),
        ku("broken", superseded_by=["missing"]),
        ku("dup-a", fingerprint="same"),
        ku("dup-b", fingerprint="same"),
        ku("stale", grade="unverified", valid_until=now),
    ]
    result = ku_lint(KuLintConfig(unverified_grade_ratio_warn=0.99), KuLintInput(knowledge_units=units), tmp_path)
    rules = {item["rule"] for item in result["findings"]["issues"]}
    assert {"orphan_ku", "broken_relation", "duplicate_fingerprint", "stale_grade"} <= rules

    good = [ku("good", substrate=["s1"], fingerprint="unique", grade="verified")]
    result = ku_lint(KuLintConfig(), KuLintInput(knowledge_units=good), tmp_path / "good")
    assert result["findings"]["issue_count"] == 0


def test_lint_is_deep_read_only(tmp_path):
    units = [ku("one", concepts=["a"], related_to=[])]
    before = deepcopy(units)
    ku_lint(KuLintConfig(), KuLintInput(knowledge_units=units), tmp_path)
    assert units == before


def test_on_step_callback(tmp_path):
    steps = []
    ku_lint(KuLintConfig(), KuLintInput(knowledge_units=[ku("one")]), tmp_path, on_step=lambda *args: steps.append(args))
    assert steps
