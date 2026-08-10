"""decision_ledger / kg_reasoning / provenance_w3c — semantica 能力 3O 化单测。"""

from __future__ import annotations

import json

from omodul.decision_ledger import (
    DecisionLedgerConfig,
    DecisionLedgerInput,
    decision_ledger,
)
from omodul.kg_reasoning import KgReasoningConfig, KgReasoningInput, kg_reasoning
from omodul.provenance_w3c import ProvenanceW3cConfig, ProvenanceW3cInput, provenance_w3c


# ── decision_ledger ───────────────────────────────────────────────────

def test_decision_ledger_record_link_trace(tmp_path):
    cfg = DecisionLedgerConfig()
    # record 3 个决策
    ids = []
    for cat, sc, out in [
        ("loan_underwriting", "个人贷款 8.5w 收入 31% DTI", "approved"),
        ("interest_rate", "获批贷款定级 B2", "rate_8.9pct"),
        ("loan_underwriting", "大额贷款 30w 收入 60% DTI", "manual_review"),
    ]:
        r = decision_ledger(cfg, DecisionLedgerInput(
            action="record", category=cat, scenario=sc, outcome=out,
            confidence=0.9, decision_maker="alice",
        ), tmp_path)
        assert r["status"] == "completed"
        ids.append(r["findings"]["decision_id"])
    # 因果链
    decision_ledger(cfg, DecisionLedgerInput(
        action="link", src_id=ids[0], dst_id=ids[1], relationship_type="CAUSED"), tmp_path)
    decision_ledger(cfg, DecisionLedgerInput(
        action="link", src_id=ids[0], dst_id=ids[2], relationship_type="INFLUENCED"), tmp_path)
    # trace
    tr = decision_ledger(cfg, DecisionLedgerInput(action="trace", decision_id=ids[1]), tmp_path)
    assert tr["findings"]["decision_id"] == ids[1]
    assert len(tr["findings"]["chain"]) >= 2  # 含自己 + 上游
    # 先例检索: 类似场景应命中 ids[0]
    q = decision_ledger(cfg, DecisionLedgerInput(
        action="query_similar", query_text="个人贷款 8.5w 收入 31% DTI", max_results=3), tmp_path)
    assert q["findings"]["precedents"][0]["decision_id"] == ids[0]
    # 策略门: confidence 阈值
    rules = decision_ledger(cfg, DecisionLedgerInput(
        action="rules", rules=[{"rule": "confidence_min", "min": 0.95}]), tmp_path)
    assert rules["findings"]["passed"] == 0  # 全部 0.9 < 0.95
    # 导出
    exp = decision_ledger(cfg, DecisionLedgerInput(action="export", format="json"), tmp_path)
    assert exp["findings"]["decisions"] == 3
    exp_prov = decision_ledger(cfg, DecisionLedgerInput(action="export", format="prov_o"), tmp_path)
    assert "prov-o" in exp_prov["findings"]["path"] or "provenance" in exp_prov["findings"]["path"]
    # 持久化: 新实例读同一文件
    r2 = decision_ledger(cfg, DecisionLedgerInput(action="list"), tmp_path)
    assert r2["findings"]["count"] == 3


def test_decision_ledger_validation(tmp_path):
    cfg = DecisionLedgerConfig()
    try:
        decision_ledger(cfg, DecisionLedgerInput(action="record", scenario="", outcome="x"), tmp_path)
        raise AssertionError("应拒绝空 scenario")
    except ValueError:
        pass
    try:
        decision_ledger(cfg, DecisionLedgerInput(
            action="link", src_id="nonexistent", dst_id="nonexistent2",
            relationship_type="CAUSED"), tmp_path)
        raise AssertionError("应拒绝不存在 src")
    except ValueError:
        pass
    try:
        decision_ledger(cfg, DecisionLedgerInput(
            action="link", src_id="a", dst_id="b", relationship_type="BOGUS"), tmp_path)
        raise AssertionError("应拒绝非法关系类型")
    except ValueError:
        pass


# ── kg_reasoning ──────────────────────────────────────────────────────

def test_kg_reasoning_transitive(tmp_path):
    cfg = KgReasoningConfig(max_iterations=5)
    # 传递规则: influences(X,Y) :- works_at(X,Z), partner_of(Z,Y)
    r = kg_reasoning(cfg, KgReasoningInput(
        facts=[["works_at", "alice", "acme"], ["partner_of", "acme", "globex"]],
        rules=["influences(X, Y) :- works_at(X, Z), partner_of(Z, Y)"],
        query="influences(alice, globex)",
    ), tmp_path)
    assert r["findings"]["derivable"] is True
    assert r["findings"]["explanation"], "应有推导链"
    # 反向查询不可推导
    r2 = kg_reasoning(cfg, KgReasoningInput(
        facts=[["works_at", "alice", "acme"]],
        rules=[],
        query="influences(alice, globex)",
    ), tmp_path)
    assert r2["findings"]["derivable"] is False


def test_kg_reasoning_derived_count(tmp_path):
    cfg = KgReasoningConfig()
    r = kg_reasoning(cfg, KgReasoningInput(
        facts=[["a", "x", "y"], ["b", "y", "z"]],
        rules=["c(X, Z) :- a(X, Y), b(Y, Z)"],
    ), tmp_path)
    assert r["findings"]["derived_facts"] == 1
    assert ["c", "x", "z"] in r["findings"]["derived"]


# ── provenance_w3c ────────────────────────────────────────────────────

def test_provenance_w3c_json_and_turtle(tmp_path):
    cfg = ProvenanceW3cConfig()
    entities = [
        {"id": "decision:d1", "type": "Decision", "value": "approved",
         "attrs": {"category": "loan", "confidence": "0.9"}},
    ]
    relations = [{"kind": "wasAttributedTo", "src": "decision:d1", "dst": "agent:alice"}]
    j = provenance_w3c(cfg, ProvenanceW3cInput(
        entities=entities, relations=relations, format="json"), tmp_path)
    assert j["findings"]["entities"] == 1
    payload = json.loads((tmp_path / "provenance.json").read_text())
    assert payload["entities"][0]["@type"] == ["prov:Entity", "veya:Decision"]
    t = provenance_w3c(cfg, ProvenanceW3cInput(
        entities=entities, relations=relations, format="turtle"), tmp_path)
    ttl = (tmp_path / "provenance.ttl").read_text()
    assert "a prov:Entity, veya:Decision" in ttl
    assert "prov:wasAttributedTo" in ttl
