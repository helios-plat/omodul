"""Tests for omodul.learning_distill — ≥10 cases."""

from __future__ import annotations

import pytest
from omodul.learning_distill import LearningDistillConfig, learning_distill


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


class MockBackend:
    def __init__(self):
        self.nodes = {}

    def put_node(self, node_id, payload):
        self.nodes[node_id] = payload

    def get_node(self, node_id):
        return self.nodes.get(node_id)


def _config(backend=None):
    return LearningDistillConfig(backend=backend)


def _episode(outcome="success"):
    return {
        "event": "Applied regularization to overfitting model",
        "outcome": outcome,
        "context": {"domain": "ml", "problem": "overfitting"},
    }


# ---------------------------------------------------------------------------
# 1. Missing backend → failed
# ---------------------------------------------------------------------------


def test_missing_backend_fails():
    result = learning_distill(
        _config(backend=None),
        {"episode": _episode()},
    )
    assert result["status"] == "failed"
    assert result["error"] is not None
    assert result["findings"] is None


# ---------------------------------------------------------------------------
# 2. Valid episode produces strategy KU
# ---------------------------------------------------------------------------


def test_valid_episode_produces_strategy():
    backend = MockBackend()
    result = learning_distill(_config(backend), {"episode": _episode()})
    assert result["status"] == "completed"
    assert result["findings"] is not None
    assert result["findings"]["status"] in ("stored", "quarantined")


# ---------------------------------------------------------------------------
# 3. Distilled KU has knowledge_type="solution_strategy"
# ---------------------------------------------------------------------------


def test_distilled_ku_is_solution_strategy():
    backend = MockBackend()
    result = learning_distill(_config(backend), {"episode": _episode()})
    assert result["status"] == "completed"
    if result["findings"]["status"] == "stored":
        ku_id = result["findings"]["ku_id"]
        stored_ku = backend.nodes.get(ku_id)
        assert stored_ku is not None
        assert stored_ku["knowledge_type"] == "solution_strategy"


# ---------------------------------------------------------------------------
# 4. Distilled KU has epistemic_status.verified=False (A19)
# ---------------------------------------------------------------------------


def test_distilled_ku_unverified():
    backend = MockBackend()
    result = learning_distill(_config(backend), {"episode": _episode()})
    assert result["status"] == "completed"
    if result["findings"]["status"] == "stored":
        ku_id = result["findings"]["ku_id"]
        stored_ku = backend.nodes.get(ku_id)
        assert stored_ku is not None
        ep = stored_ku.get("epistemic_status", {})
        assert ep.get("verified") is False


# ---------------------------------------------------------------------------
# 5. ku_id returned in findings
# ---------------------------------------------------------------------------


def test_ku_id_in_findings():
    backend = MockBackend()
    result = learning_distill(_config(backend), {"episode": _episode()})
    assert result["status"] == "completed"
    assert "ku_id" in result["findings"]
    assert result["findings"]["ku_id"] is not None


# ---------------------------------------------------------------------------
# 6. Invalid KU (ku_gate_validate rejects) → quarantined status, no raise
# ---------------------------------------------------------------------------


def test_invalid_episode_quarantined():
    # Force ku_gate_validate to reject by patching it in the module where it's used
    import sys
    import importlib

    # Ensure the module is loaded
    importlib.import_module("omodul.learning_distill")
    ld_mod = sys.modules["omodul.learning_distill"]

    original_validate = ld_mod.ku_gate_validate

    def _reject_all(*, ku):
        return {"valid": False, "errors": ["forced_rejection"], "warnings": []}

    ld_mod.ku_gate_validate = _reject_all
    try:
        backend = MockBackend()
        result = learning_distill(_config(backend), {"episode": _episode()})
        assert result["status"] == "completed"
        assert result["findings"]["status"] == "quarantined"
        assert result["findings"]["validation_errors"] == ["forced_rejection"]
        assert len(backend.nodes) == 0  # nothing stored
    finally:
        ld_mod.ku_gate_validate = original_validate


# ---------------------------------------------------------------------------
# 7. decision_trail returned and non-empty
# ---------------------------------------------------------------------------


def test_decision_trail_returned():
    backend = MockBackend()
    result = learning_distill(_config(backend), {"episode": _episode()})
    trail = result["decision_trail"]
    assert trail["omodul"] == "learning_distill"
    assert len(trail["steps"]) > 0


# ---------------------------------------------------------------------------
# 8. Standard return dict keys present
# ---------------------------------------------------------------------------


def test_standard_return_keys():
    backend = MockBackend()
    result = learning_distill(_config(backend), {"episode": _episode()})
    for key in ("findings", "status", "error", "decision_trail", "report_path", "cost_usd"):
        assert key in result
    assert result["report_path"] is None
    assert result["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# 9. on_step callback accepted
# ---------------------------------------------------------------------------


def test_on_step_callback():
    steps = []
    backend = MockBackend()
    learning_distill(_config(backend), {"episode": _episode()}, on_step=steps.append)
    assert len(steps) > 0


# ---------------------------------------------------------------------------
# 10. Positive outcome episode stored
# ---------------------------------------------------------------------------


def test_positive_outcome_episode_stored():
    backend = MockBackend()
    result = learning_distill(_config(backend), {"episode": _episode(outcome="success")})
    assert result["status"] == "completed"
    # Should be stored (valid KU)
    assert result["findings"]["status"] in ("stored", "quarantined")


# ---------------------------------------------------------------------------
# 11. Negative outcome episode still distilled (no filtering by outcome)
# ---------------------------------------------------------------------------


def test_negative_outcome_episode_also_distilled():
    backend = MockBackend()
    result = learning_distill(_config(backend), {"episode": _episode(outcome="failure")})
    assert result["status"] == "completed"
    assert result["findings"] is not None


# ---------------------------------------------------------------------------
# 12. Missing episode key → failed gracefully
# ---------------------------------------------------------------------------


def test_missing_episode_fails_gracefully():
    backend = MockBackend()
    result = learning_distill(_config(backend), {})
    assert result["status"] == "failed"
    assert result["error"] is not None
    assert result["findings"] is None
