from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from omodul.ku_health import list_open_issues, persist_issues
from omodul.ku_heal_cycle import KuHealCycleConfig, KuHealCycleInput, ku_heal_cycle
from omodul.ku_lint import KuLintConfig, KuLintInput, lint_knowledge_units


def ku(ku_id, **extra):
    value = {"ku_id": ku_id, "knowledge_type": "factual", "grade": "verified", "substrate": ["s1"]}
    value.update(extra)
    return value


class FakeBackend:
    """规格 5 的注入接口 (测试用): put_node / get_node / nodes。

    pydantic v2 对 list[dict] 输入会拷贝元素, 调用方原列表不可变;
    heal 的确定性修复因此以 backend 为可见载体 (规格 4/5 设计意图)。
    """

    def __init__(self, units):
        self.nodes = {u["ku_id"]: u for u in units}

    def put_node(self, ku_id, ku):
        self.nodes[ku_id] = ku

    def get_node(self, ku_id):
        return self.nodes.get(ku_id)


def test_heal_cycle_repairs_and_resolves(tmp_path):
    units = [
        ku("keeper", fingerprint="same"),
        ku("duplicate", fingerprint="same"),
        ku("broken", related_to=["missing"]),
        ku("orphan", substrate=[]),
        ku("stale", grade="unverified",
           valid_until=(datetime.now(UTC) - timedelta(days=1)).isoformat()),
    ]
    issues = lint_knowledge_units(KuLintConfig(unverified_grade_ratio_warn=0.99), KuLintInput(knowledge_units=units))
    persist_issues(tmp_path, issues)
    backend = FakeBackend(units)
    result = ku_heal_cycle(
        KuHealCycleConfig(issue_dir=tmp_path, unverified_grade_ratio_warn=0.99),
        KuHealCycleInput(knowledge_units=units, backend=backend),
        tmp_path,
    )
    assert result["status"] == "completed"
    # 确定性修复写入 backend (规格 4/5: 修复载体 = backend 注入)
    assert backend.nodes["duplicate"]["status"] == "deprecated"
    assert backend.nodes["keeper"].get("status") is None  # 最早出现的 KU 保留
    assert "related_to" not in backend.nodes["broken"]
    assert backend.nodes["orphan"]["orphan"] is True
    assert backend.nodes["stale"]["stale_grade"] is True
    assert backend.nodes["stale"]["grade"] == "unverified"  # stale 不自动改 grade
    assert not list_open_issues(tmp_path)
    records = [json.loads(line) for line in (tmp_path / "issues.jsonl").read_text().splitlines()]
    assert records and all(record["status"] == "resolved" for record in records)
    assert all(item["ok"] for item in result["findings"]["actions"])


def test_heal_callback(tmp_path):
    units = [ku("orphan", substrate=[])]
    persist_issues(tmp_path, lint_knowledge_units(KuLintConfig(), KuLintInput(knowledge_units=units)))
    steps = []
    ku_heal_cycle(KuHealCycleConfig(issue_dir=tmp_path), KuHealCycleInput(knowledge_units=units), on_step=lambda *args: steps.append(args))
    assert steps
