"""Tests for omodul.knowledge_reflux."""

from __future__ import annotations

import json
import os

import pytest

from omodul.knowledge_reflux import (
    KnowledgeRefluxConfig,
    RefluxReport,
    check_dangling,
    check_contradictions,
    check_missing_inverse,
    check_supersede_stale,
    check_missing_fields,
    compute_coherence,
    reflux,
    run_reflux,
)


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


class MockBackend:
    def __init__(self):
        self._nodes = {}
        self._edges = []

    def put_node(self, node_id, payload):
        self._nodes[node_id] = payload

    def put_edge(self, src, rel, dst):
        if (src, rel, dst) not in self._edges:
            self._edges.append((src, rel, dst))

    def get_node(self, node_id):
        return self._nodes.get(node_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_config(backend=None, auto_apply_low=True):
    return KnowledgeRefluxConfig(
        backend=backend or MockBackend(),
        auto_apply_low=auto_apply_low,
    )


def make_backend_with(nodes=None, edges=None):
    b = MockBackend()
    for nid, payload in (nodes or {}).items():
        b._nodes[nid] = payload
    for edge in edges or []:
        b._edges.append(edge)
    return b


# ---------------------------------------------------------------------------
# Tests: pure check functions
# ---------------------------------------------------------------------------


def test_empty_graph_returns_no_findings():
    """An empty graph produces no findings from any check."""
    report = reflux(MockBackend())
    assert report.findings == []
    assert report.auto_applied == []
    assert report.needs_review == []


def test_dangling_reference_detected():
    """An edge pointing to a non-existent node is flagged as dangling."""
    b = make_backend_with(
        nodes={"A": {"title": "A"}},
        edges=[("A", "depends_on", "GHOST")],
    )
    findings = check_dangling({"A": {"title": "A"}}, [("A", "depends_on", "GHOST")])
    assert len(findings) == 1
    assert findings[0].kind == "dangling"
    assert findings[0].severity == "high"
    assert findings[0].detail["missing_dst"] == "GHOST"


def test_contradiction_detection_supersede_cycle():
    """A supersedes B AND B supersedes A is detected as a contradiction."""
    nodes = {"A": {"title": "A"}, "B": {"title": "B"}}
    edges = [("A", "supersedes", "B"), ("B", "supersedes", "A")]
    findings = check_contradictions(nodes, edges)
    kinds = [f.kind for f in findings]
    assert "contradiction" in kinds
    # Only one finding for the pair (deduped)
    contradiction_findings = [
        f
        for f in findings
        if f.kind == "contradiction" and f.detail.get("reason") == "supersede_cycle"
    ]
    assert len(contradiction_findings) == 1


def test_missing_inverse_relation_detected_and_auto_applied():
    """A→supports→B missing the B→supported_by→A inverse is auto-applied when auto_apply_low=True."""
    b = make_backend_with(
        nodes={"A": {"title": "A"}, "B": {"title": "B"}},
        edges=[("A", "supports", "B")],
    )
    config = make_config(backend=b, auto_apply_low=True)
    result = run_reflux(config, {})
    assert result["status"] == "completed"
    report = result["findings"]
    # The missing_inverse finding should be auto-applied
    auto_kinds = [f.kind for f in report.auto_applied]
    assert "missing_inverse" in auto_kinds
    # The inverse edge should now exist in the backend
    assert ("B", "supported_by", "A") in b._edges


def test_supersede_stale_detected():
    """A supersedes B but B's epistemic_status.truth_value != 'superseded' is flagged."""
    nodes = {
        "A": {"title": "A"},
        "B": {"title": "B", "epistemic_status": {"truth_value": "confirmed"}},
    }
    edges = [("A", "supersedes", "B")]
    findings = check_supersede_stale(nodes, edges)
    assert len(findings) == 1
    assert findings[0].kind == "supersede_stale"
    assert findings[0].severity == "high"
    assert findings[0].subject == "B"


def test_supersede_stale_not_flagged_when_already_marked():
    """If superseded node already has truth_value='superseded', no finding produced."""
    nodes = {
        "A": {"title": "A"},
        "B": {"title": "B", "epistemic_status": {"truth_value": "superseded"}},
    }
    edges = [("A", "supersedes", "B")]
    findings = check_supersede_stale(nodes, edges)
    assert findings == []


def test_missing_required_fields_detected():
    """A KU node missing symbolic_form/vector/epistemic_status is flagged."""
    nodes = {
        "KU-1": {"is_ku": True, "title": "Some KU"},  # missing all three fields
    }
    findings = check_missing_fields(nodes)
    assert len(findings) == 1
    assert findings[0].kind == "missing_fields"
    assert findings[0].severity == "high"
    assert set(findings[0].detail["missing"]) == {"symbolic_form", "vector", "epistemic_status"}


def test_non_ku_nodes_not_checked_for_missing_fields():
    """Nodes without is_ku=True are not checked for required fields."""
    nodes = {
        "plain-node": {"title": "Just a plain node"},
    }
    findings = check_missing_fields(nodes)
    assert findings == []


# ---------------------------------------------------------------------------
# Tests: run_reflux standard dict
# ---------------------------------------------------------------------------


def test_run_reflux_returns_standard_dict_keys():
    """run_reflux returns dict with all required keys."""
    config = make_config()
    result = run_reflux(config, {})
    for key in ("findings", "status", "error", "decision_trail", "report_path", "cost_usd"):
        assert key in result, f"Missing key: {key}"
    assert result["report_path"] is None
    assert result["cost_usd"] == 0.0


def test_run_reflux_status_completed_on_success():
    """run_reflux returns status=completed when backend is valid."""
    config = make_config()
    result = run_reflux(config, {})
    assert result["status"] == "completed"
    assert result["error"] is None


def test_run_reflux_findings_is_reflux_report():
    """findings is a RefluxReport instance on success."""
    config = make_config()
    result = run_reflux(config, {})
    assert isinstance(result["findings"], RefluxReport)


def test_reflux_report_summary_has_coherence_score_key():
    """RefluxReport.summary() returns the expected keys."""
    config = make_config()
    result = run_reflux(config, {})
    summary = result["findings"].summary()
    assert "total_findings" in summary
    assert "by_kind" in summary
    assert "auto_applied" in summary
    assert "needs_review" in summary


def test_output_dir_creates_decision_trail_json(tmp_path):
    """When output_dir is set, decision_trail.json is written to disk."""
    config = make_config()
    out_dir = str(tmp_path / "reflux_trail")
    result = run_reflux(config, {}, output_dir=out_dir)
    trail_file = os.path.join(out_dir, "decision_trail.json")
    assert os.path.exists(trail_file)
    with open(trail_file) as f:
        on_disk = json.load(f)
    assert on_disk["omodul"] == "knowledge_reflux"
    assert on_disk["status"] == result["decision_trail"]["status"]


def test_on_step_callback_called():
    """on_step callback is invoked for each trail step."""
    config = make_config()
    seen_steps = []
    run_reflux(config, {}, on_step=lambda s: seen_steps.append(s["step"]))
    assert "reflux_start" in seen_steps
    assert "reflux_done" in seen_steps


def test_decision_trail_has_omodul_and_pillars():
    """decision_trail contains omodul name and enabled_pillars."""
    config = make_config()
    result = run_reflux(config, {})
    trail = result["decision_trail"]
    assert trail["omodul"] == "knowledge_reflux"
    assert "decision_trail" in trail["enabled_pillars"]


def test_symmetric_relation_auto_completed():
    """A contradicts B missing B contradicts A is auto-applied."""
    b = make_backend_with(
        nodes={"A": {"title": "A"}, "B": {"title": "B"}},
        edges=[("A", "contradicts", "B")],
    )
    config = make_config(backend=b, auto_apply_low=True)
    result = run_reflux(config, {})
    assert result["status"] == "completed"
    # inverse edge should be added
    assert ("B", "contradicts", "A") in b._edges


def test_auto_apply_low_false_keeps_findings_in_needs_review():
    """When auto_apply_low=False, missing_inverse findings stay in needs_review."""
    b = make_backend_with(
        nodes={"A": {"title": "A"}, "B": {"title": "B"}},
        edges=[("A", "supports", "B")],
    )
    config = make_config(backend=b, auto_apply_low=False)
    result = run_reflux(config, {})
    report = result["findings"]
    assert report.auto_applied == []
    review_kinds = [f.kind for f in report.needs_review]
    assert "missing_inverse" in review_kinds


# ---------------------------------------------------------------------------
# Protocol backend (list_nodes / list_edges) — public path exercised
# ---------------------------------------------------------------------------


class ProtocolBackend:
    """Implements list_nodes() / list_edges(nid) / get_node() — no _nodes attr."""

    def __init__(self):
        self._store: dict = {}
        self._edge_list: list = []

    def put_node(self, nid, payload):
        self._store[nid] = payload

    def get_node(self, nid):
        return self._store.get(nid)

    def list_nodes(self):
        return list(self._store.keys())

    def put_edge(self, src, rel, dst):
        self._edge_list.append((src, rel, dst))

    def list_edges(self, nid):
        from types import SimpleNamespace

        return [
            SimpleNamespace(src_id=s, relation=r, dst_id=d)
            for s, r, d in self._edge_list
            if s == nid
        ]


def test_protocol_backend_list_nodes_path():
    """_graph_snapshot uses list_nodes() when backend exposes it."""
    b = ProtocolBackend()
    b.put_node("N1", {"title": "Node 1"})
    b.put_node("N2", {"title": "Node 2"})
    b.put_edge("N1", "supports", "N2")
    config = make_config(backend=b)
    result = run_reflux(config, {})
    assert result["status"] == "completed"
    # Both nodes visible via list_nodes() path → reflux ran without error
    summary = result["findings"].summary()
    assert "total_findings" in summary


def test_protocol_backend_dangling_ref_detected():
    """Dangling reference is detected via list_nodes() path."""
    b = ProtocolBackend()
    b.put_node("N1", {"title": "Node 1"})
    b.put_edge("N1", "supports", "MISSING")
    config = make_config(backend=b)
    result = run_reflux(config, {})
    assert result["status"] == "completed"
    finding_kinds = [f.kind for f in result["findings"].findings]
    assert "dangling" in finding_kinds
