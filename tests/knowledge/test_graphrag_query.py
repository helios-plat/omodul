"""Tests for omodul.graphrag_query — ≥11 tests."""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

import pytest

from omodul.graphrag_query import GraphRAGQueryConfig, graphrag_query


# ---------------------------------------------------------------------------
# MockBackend
# ---------------------------------------------------------------------------


class MockBackend:
    def __init__(self):
        self._nodes = {}
        self._edges = set()

    def put_node(self, nid, payload):
        self._nodes[nid] = payload

    def get_node(self, nid):
        return self._nodes.get(nid)

    def put_edge(self, s, r, d):
        self._edges.add((s, r, d))

    def list_edges(self, nid):
        return [
            SimpleNamespace(src_id=s, relation=r, dst_id=d) for s, r, d in self._edges if s == nid
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**kwargs):
    return GraphRAGQueryConfig(**kwargs)


# ---------------------------------------------------------------------------
# 1. Missing backend → status=failed, no raise
# ---------------------------------------------------------------------------


def test_missing_backend_status_failed():
    config = _make_config()  # backend=None by default
    result = graphrag_query(config, {"query": "test"})
    assert result["status"] == "failed"
    assert result["error"] is not None
    assert "ERR_GRAPHRAG" in result["error"]["code"]


# ---------------------------------------------------------------------------
# 2. Empty backend → findings.results = []
# ---------------------------------------------------------------------------


def test_empty_backend_empty_results():
    backend = MockBackend()
    config = _make_config(backend=backend)
    result = graphrag_query(config, {"query": "anything"})
    assert result["status"] == "completed"
    assert result["findings"]["results"] == []


# ---------------------------------------------------------------------------
# 3. KU with matching text is retrieved
# ---------------------------------------------------------------------------


def test_ku_with_matching_text_retrieved():
    backend = MockBackend()
    backend.put_node(
        "KU-001",
        {
            "natural_text": "machine learning neural networks",
            "epistemic_status": {"grade": "unverified"},
        },
    )
    config = _make_config(backend=backend)
    result = graphrag_query(config, {"query": "neural networks"})
    assert result["status"] == "completed"
    ku_ids = [r["ku_id"] for r in result["findings"]["results"]]
    assert "KU-001" in ku_ids


# ---------------------------------------------------------------------------
# 4. decision_trail returned with steps
# ---------------------------------------------------------------------------


def test_decision_trail_has_steps():
    backend = MockBackend()
    config = _make_config(backend=backend)
    result = graphrag_query(config, {"query": "test"})
    dt = result["decision_trail"]
    assert "steps" in dt
    assert isinstance(dt["steps"], list)
    assert len(dt["steps"]) >= 2


# ---------------------------------------------------------------------------
# 5. findings has "results" and "total_candidates" keys
# ---------------------------------------------------------------------------


def test_findings_has_required_keys():
    backend = MockBackend()
    config = _make_config(backend=backend)
    result = graphrag_query(config, {"query": "test"})
    assert "results" in result["findings"]
    assert "total_candidates" in result["findings"]


# ---------------------------------------------------------------------------
# 6. Results sorted by score descending
# ---------------------------------------------------------------------------


def test_results_sorted_by_score_descending():
    backend = MockBackend()
    backend.put_node(
        "KU-A",
        {
            "natural_text": "alpha topic relevant match",
            "epistemic_status": {"grade": "unverified"},
        },
    )
    backend.put_node(
        "KU-B",
        {
            "natural_text": "beta totally different content xyz",
            "epistemic_status": {"grade": "unverified"},
        },
    )
    config = _make_config(backend=backend)
    result = graphrag_query(config, {"query": "alpha topic relevant match"})
    scores = [r["score"] for r in result["findings"]["results"]]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# 7. min_grade filter: unverified KUs filtered out if min_grade="moderate"
# ---------------------------------------------------------------------------


def test_min_grade_filter_removes_unverified():
    backend = MockBackend()
    backend.put_node(
        "KU-unverified",
        {
            "natural_text": "some knowledge",
            "epistemic_status": {"grade": "unverified"},
        },
    )
    backend.put_node(
        "KU-moderate",
        {
            "natural_text": "some knowledge moderate",
            "epistemic_status": {"grade": "moderate"},
        },
    )
    config = _make_config(backend=backend, min_grade="moderate")
    result = graphrag_query(config, {"query": "some knowledge"})
    ku_ids = [r["ku_id"] for r in result["findings"]["results"]]
    assert "KU-unverified" not in ku_ids
    assert "KU-moderate" in ku_ids


# ---------------------------------------------------------------------------
# 8. seed_ids trigger graph expansion (graph hits appear in trail)
# ---------------------------------------------------------------------------


def test_seed_ids_trigger_graph_expansion():
    backend = MockBackend()
    backend.put_node(
        "KU-seed", {"natural_text": "seed node", "epistemic_status": {"grade": "unverified"}}
    )
    backend.put_node(
        "KU-neighbor",
        {"natural_text": "neighbor node", "epistemic_status": {"grade": "unverified"}},
    )
    backend.put_edge("KU-seed", "related_to", "KU-neighbor")
    config = _make_config(backend=backend)
    result = graphrag_query(config, {"query": "", "seed_ids": ["KU-seed"]})
    steps = result["decision_trail"]["steps"]
    graph_step = next((s for s in steps if s.get("step") == "entity_graph_search"), None)
    assert graph_step is not None
    assert graph_step["seeds"] == 1


# ---------------------------------------------------------------------------
# 9. Standard return dict keys present
# ---------------------------------------------------------------------------


def test_standard_return_keys():
    backend = MockBackend()
    config = _make_config(backend=backend)
    result = graphrag_query(config, {"query": "test"})
    for key in ["findings", "status", "error", "decision_trail", "report_path", "cost_usd"]:
        assert key in result


# ---------------------------------------------------------------------------
# 10. on_step parameter accepted without error
# ---------------------------------------------------------------------------


def test_on_step_accepted():
    backend = MockBackend()
    config = _make_config(backend=backend)
    called = []

    def step_cb(info):
        called.append(info)

    result = graphrag_query(config, {"query": "test"}, on_step=step_cb)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 11. Empty query still returns result structure (graph hits may apply)
# ---------------------------------------------------------------------------


def test_empty_query_returns_structure():
    backend = MockBackend()
    backend.put_node(
        "KU-X",
        {
            "natural_text": "some node",
            "epistemic_status": {"grade": "unverified"},
        },
    )
    config = _make_config(backend=backend)
    result = graphrag_query(config, {"query": ""})
    assert result["status"] == "completed"
    assert "results" in result["findings"]


# ---------------------------------------------------------------------------
# 12. decision_trail written to output_dir
# ---------------------------------------------------------------------------


def test_output_dir_writes_decision_trail():
    backend = MockBackend()
    config = _make_config(backend=backend)
    with tempfile.TemporaryDirectory() as tmpdir:
        result = graphrag_query(config, {"query": "test"}, output_dir=tmpdir)
        trail_path = os.path.join(tmpdir, "decision_trail.json")
        assert os.path.exists(trail_path)


# ---------------------------------------------------------------------------
# 13. Config dict input accepted
# ---------------------------------------------------------------------------


def test_config_dict_accepted():
    backend = MockBackend()
    config_dict = {"backend": backend}
    result = graphrag_query(config_dict, {"query": "test"})
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 14. Protocol backend (list_nodes / list_edges) — public path exercised
# ---------------------------------------------------------------------------


class ProtocolBackend:
    """Implements list_nodes() / list_edges() / get_node() — no _nodes attr."""

    def __init__(self):
        self._store: dict = {}
        self._edges: dict = {}

    def put_node(self, nid, payload):
        self._store[nid] = payload

    def get_node(self, nid):
        return self._store.get(nid)

    def list_nodes(self):
        return list(self._store.keys())

    def put_edge(self, s, r, d):
        self._edges.setdefault(s, []).append(SimpleNamespace(src_id=s, relation=r, dst_id=d))

    def list_edges(self, nid):
        return self._edges.get(nid, [])


def test_protocol_backend_list_nodes_path():
    """graphrag_query uses list_nodes() when backend exposes it (no _nodes access)."""
    backend = ProtocolBackend()
    backend.put_node(
        "KU-P1",
        {
            "natural_text": "protocol node content",
            "epistemic_status": {"grade": "high"},
            "knowledge_type": "principle",
        },
    )
    config = _make_config(backend=backend)
    result = graphrag_query(config, {"query": "protocol node"})
    assert result["status"] == "completed"
    assert len(result["findings"]["results"]) >= 1
    hit = result["findings"]["results"][0]
    assert hit["ku_id"] == "KU-P1"
    assert hit["knowledge_type"] == "principle"


def test_protocol_backend_knowledge_type_in_result():
    """knowledge_type field is present in every result entry."""
    backend = ProtocolBackend()
    backend.put_node(
        "KU-P2",
        {
            "natural_text": "another principle",
            "epistemic_status": {"grade": "medium"},
            "knowledge_type": "heuristic",
        },
    )
    config = _make_config(backend=backend)
    result = graphrag_query(config, {"query": "another"})
    assert result["status"] == "completed"
    for entry in result["findings"]["results"]:
        assert "knowledge_type" in entry
