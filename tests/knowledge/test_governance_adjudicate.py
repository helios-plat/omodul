"""Tests for omodul.governance_adjudicate — ≥10 cases."""

from __future__ import annotations

import pytest
from omodul.governance_adjudicate import GovernanceAdjudicateConfig, governance_adjudicate


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


class MockBackend:
    def __init__(self, nodes=None):
        self.nodes = dict(nodes or {})

    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def put_node(self, node_id, payload):
        self.nodes[node_id] = payload


def _config(backend=None):
    return GovernanceAdjudicateConfig(backend=backend)


def _input(risk_level=0, action="update_ku", subject_ku_id=None, evidence=None):
    d = {"action": action, "risk_level": risk_level}
    if subject_ku_id is not None:
        d["subject_ku_id"] = subject_ku_id
    if evidence is not None:
        d["evidence"] = evidence
    return d


def _valid_evidence_ku():
    return {
        "ku_id": "KU-EV-001",
        "knowledge_type": "proposition",
        "natural_text": "Evidence for the action.",
        "epistemic_status": {"grade": "moderate", "verified": False},
    }


# ---------------------------------------------------------------------------
# 1. L0 action → auto_approved
# ---------------------------------------------------------------------------


def test_l0_auto_approved():
    result = governance_adjudicate(_config(), _input(risk_level=0))
    assert result["status"] == "completed"
    assert result["findings"]["decision"] == "auto_approved"
    assert result["findings"]["tier"] == "L0"
    assert result["findings"]["escalation_required"] is False


# ---------------------------------------------------------------------------
# 2. L1 action → needs_review
# ---------------------------------------------------------------------------


def test_l1_needs_review():
    result = governance_adjudicate(_config(), _input(risk_level=1))
    assert result["status"] == "completed"
    assert result["findings"]["decision"] == "needs_review"
    assert result["findings"]["tier"] == "L1"
    assert result["findings"]["escalation_required"] is False


# ---------------------------------------------------------------------------
# 3. L2 action → needs_review
# ---------------------------------------------------------------------------


def test_l2_needs_review():
    result = governance_adjudicate(_config(), _input(risk_level=2))
    assert result["status"] == "completed"
    assert result["findings"]["decision"] == "needs_review"
    assert result["findings"]["tier"] == "L2"


# ---------------------------------------------------------------------------
# 4. L3 action → escalate
# ---------------------------------------------------------------------------


def test_l3_escalate():
    result = governance_adjudicate(_config(), _input(risk_level=3))
    assert result["status"] == "completed"
    assert result["findings"]["decision"] == "escalate"
    assert result["findings"]["tier"] == "L3"
    assert result["findings"]["escalation_required"] is True


# ---------------------------------------------------------------------------
# 5. L4 action → escalate
# ---------------------------------------------------------------------------


def test_l4_escalate():
    result = governance_adjudicate(_config(), _input(risk_level=4))
    assert result["status"] == "completed"
    assert result["findings"]["decision"] == "escalate"
    assert result["findings"]["tier"] == "L4"
    assert result["findings"]["escalation_required"] is True


# ---------------------------------------------------------------------------
# 6. escalation_required True for L3/L4
# ---------------------------------------------------------------------------


def test_escalation_required_l3_l4():
    for level in (3, 4):
        result = governance_adjudicate(_config(), _input(risk_level=level))
        assert result["findings"]["escalation_required"] is True


# ---------------------------------------------------------------------------
# 7. decision_trail returned and non-empty
# ---------------------------------------------------------------------------


def test_decision_trail_returned():
    result = governance_adjudicate(_config(), _input(risk_level=0))
    trail = result["decision_trail"]
    assert trail["omodul"] == "governance_adjudicate"
    assert len(trail["steps"]) > 0


# ---------------------------------------------------------------------------
# 8. Standard return dict keys present
# ---------------------------------------------------------------------------


def test_standard_return_keys():
    result = governance_adjudicate(_config(), _input(risk_level=0))
    for key in ("findings", "status", "error", "decision_trail", "report_path", "cost_usd"):
        assert key in result
    assert result["report_path"] is None
    assert result["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# 9. Missing backend graceful (coherence check skipped)
# ---------------------------------------------------------------------------


def test_missing_backend_graceful():
    # No backend but has subject_ku_id — coherence check skipped, not a failure
    result = governance_adjudicate(
        _config(backend=None),
        _input(risk_level=1, subject_ku_id="KU-001"),
    )
    assert result["status"] == "completed"
    assert result["findings"]["decision"] == "needs_review"


# ---------------------------------------------------------------------------
# 10. Invalid evidence KU → validation errors in trail, still completes
# ---------------------------------------------------------------------------


def test_invalid_evidence_ku_captured():
    bad_ku = {"ku_id": "KU-BAD", "knowledge_type": "INVALID_TYPE", "epistemic_status": {}}
    result = governance_adjudicate(_config(), _input(risk_level=1, evidence=bad_ku))
    assert result["status"] == "completed"
    findings = result["findings"]
    # validation errors should be captured
    assert len(findings["validation_errors"]) > 0


# ---------------------------------------------------------------------------
# 11. Valid evidence KU → no validation errors
# ---------------------------------------------------------------------------


def test_valid_evidence_ku_no_errors():
    result = governance_adjudicate(_config(), _input(risk_level=0, evidence=_valid_evidence_ku()))
    assert result["status"] == "completed"
    assert result["findings"]["validation_errors"] == []


# ---------------------------------------------------------------------------
# 12. All risk levels 0-4 handled without error
# ---------------------------------------------------------------------------


def test_all_risk_levels_handled():
    for level in range(5):
        result = governance_adjudicate(_config(), _input(risk_level=level))
        assert result["status"] == "completed"
        assert result["findings"] is not None


# ---------------------------------------------------------------------------
# 13. justification string non-empty
# ---------------------------------------------------------------------------


def test_justification_non_empty():
    for level in range(5):
        result = governance_adjudicate(_config(), _input(risk_level=level))
        assert isinstance(result["findings"]["justification"], str)
        assert len(result["findings"]["justification"]) > 0


# ---------------------------------------------------------------------------
# 14. on_step callback accepted
# ---------------------------------------------------------------------------


def test_on_step_callback():
    steps = []
    governance_adjudicate(_config(), _input(risk_level=2), on_step=steps.append)
    assert len(steps) > 0
