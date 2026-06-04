"""Tests for omodul.reuse_strategy."""

from __future__ import annotations

import pytest

from omodul.reuse_strategy import ReuseStrategyConfig, reuse_strategy


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


class MockBackend:
    def __init__(self, nodes=None):
        self._nodes = dict(nodes or {})

    def put_node(self, node_id, payload):
        self._nodes[node_id] = payload

    def get_node(self, node_id):
        return self._nodes.get(node_id)

    def list_nodes(self):
        return list(self._nodes.values())


def _strategy_ku(ku_id, natural_text, grade="unverified", **kwargs):
    ku = {
        "ku_id": ku_id,
        "knowledge_type": "solution_strategy",
        "natural_text": natural_text,
        "symbolic_form": {"title": natural_text},
        "vector": None,
        "vector_frozen": False,
        "epistemic_status": {
            "grade": grade,
            "source": None,
            "defeaters": [],
            "verified": False,
        },
        "provenance": {"source": "test", "chunk_id": None},
        "project_id": "test_proj",
    }
    ku.update(kwargs)
    return ku


def _make_config(backend):
    return ReuseStrategyConfig(backend=backend)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_strategies_findings_none_status_completed():
    backend = MockBackend()
    result = reuse_strategy(
        _make_config(backend),
        {"query": "how to chunk documents", "project_id": "p1"},
    )
    assert result["status"] == "completed"
    assert result["findings"] is not None
    assert result["findings"]["matched_ku_id"] is None


def test_one_matching_strategy_returns_matched_ku_id():
    ku = _strategy_ku("S-001", "chunked document retrieval strategy")
    backend = MockBackend({"S-001": ku})
    result = reuse_strategy(
        _make_config(backend),
        {"query": "chunked retrieval", "project_id": "p1"},
    )
    assert result["status"] == "completed"
    assert result["findings"]["matched_ku_id"] == "S-001"


def test_proven_strategy_recommends_reuse_true():
    ku = _strategy_ku("S-proven", "batch processing strategy", grade="proven")
    backend = MockBackend({"S-proven": ku})
    result = reuse_strategy(
        _make_config(backend),
        {"query": "batch processing", "project_id": "p1"},
    )
    assert result["findings"]["recommend_reuse"] is True
    assert result["findings"]["grade"] == "proven"


def test_high_grade_strategy_recommends_reuse_true():
    ku = _strategy_ku("S-high", "caching layer strategy", grade="high")
    backend = MockBackend({"S-high": ku})
    result = reuse_strategy(
        _make_config(backend),
        {"query": "caching layer", "project_id": "p1"},
    )
    assert result["findings"]["recommend_reuse"] is True


def test_failed_strategy_recommends_reuse_false():
    ku = _strategy_ku("S-fail", "naive brute force search", grade="failed")
    backend = MockBackend({"S-fail": ku})
    result = reuse_strategy(
        _make_config(backend),
        {"query": "brute force search", "project_id": "p1"},
    )
    assert result["findings"]["recommend_reuse"] is False
    assert result["findings"]["grade"] == "failed"


def test_low_grade_strategy_recommend_reuse_none():
    ku = _strategy_ku("S-low", "experimental strategy attempt", grade="low")
    backend = MockBackend({"S-low": ku})
    result = reuse_strategy(
        _make_config(backend),
        {"query": "experimental strategy", "project_id": "p1"},
    )
    assert result["findings"]["recommend_reuse"] is None


def test_empty_query_handled_gracefully():
    ku = _strategy_ku("S-001", "some strategy description")
    backend = MockBackend({"S-001": ku})
    result = reuse_strategy(
        _make_config(backend),
        {"query": "", "project_id": "p1"},
    )
    assert result["status"] == "completed"
    # empty query => 0 similarity with everything; still returns a result
    assert "matched_ku_id" in result["findings"]


def test_decision_trail_returned():
    backend = MockBackend()
    result = reuse_strategy(
        _make_config(backend),
        {"query": "test", "project_id": "p1"},
    )
    trail = result["decision_trail"]
    assert trail is not None
    assert "steps" in trail
    assert len(trail["steps"]) > 0


def test_standard_return_dict_keys_present():
    backend = MockBackend()
    result = reuse_strategy(
        _make_config(backend),
        {"query": "test", "project_id": "p1"},
    )
    for key in ("findings", "status", "error", "decision_trail", "report_path", "cost_usd"):
        assert key in result, f"Missing key: {key}"


def test_multiple_strategies_best_match_selected():
    """k=1: the strategy with highest token overlap should be matched."""
    ku1 = _strategy_ku("S-001", "chunked document retrieval pipeline")
    ku2 = _strategy_ku("S-002", "vector database indexing approach")
    ku3 = _strategy_ku("S-003", "chunked retrieval with overlap windows")
    backend = MockBackend({"S-001": ku1, "S-002": ku2, "S-003": ku3})
    result = reuse_strategy(
        _make_config(backend),
        {"query": "chunked retrieval", "project_id": "p1"},
    )
    assert result["status"] == "completed"
    # Best match should be S-001 or S-003 (both contain "chunked" and "retrieval")
    assert result["findings"]["matched_ku_id"] in {"S-001", "S-003"}


def test_status_completed_when_no_match():
    backend = MockBackend()
    result = reuse_strategy(
        _make_config(backend),
        {"query": "anything", "project_id": "p1"},
    )
    assert result["status"] == "completed"


def test_missing_backend_returns_failed_no_raise():
    config = ReuseStrategyConfig(backend=None)
    result = reuse_strategy(
        config,
        {"query": "test", "project_id": "p1"},
    )
    assert result["status"] == "failed"
    assert result["error"] is not None
    assert result["findings"] is None


def test_on_step_callback_invoked():
    backend = MockBackend()
    steps = []
    reuse_strategy(
        _make_config(backend),
        {"query": "test query", "project_id": "p1"},
        on_step=steps.append,
    )
    assert len(steps) > 0
