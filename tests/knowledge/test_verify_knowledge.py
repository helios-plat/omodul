"""Tests for omodul.verify_knowledge — ≥10 cases."""

from __future__ import annotations

import pytest
from omodul.verify_knowledge import VerifyKnowledgeConfig, verify_knowledge


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


def _make_ku(ku_id="KU-001", grade="unverified"):
    return {
        "ku_id": ku_id,
        "knowledge_type": "proposition",
        "natural_text": "Test knowledge unit.",
        "epistemic_status": {"grade": grade, "verified": False},
    }


def _config(backend=None):
    return VerifyKnowledgeConfig(backend=backend)


# ---------------------------------------------------------------------------
# 1. Missing backend → failed
# ---------------------------------------------------------------------------


def test_missing_backend_fails():
    result = verify_knowledge(
        _config(backend=None),
        {"ku_id": "KU-001", "verification_data": {"type": "manual", "verdict": "confirmed"}},
    )
    assert result["status"] == "failed"
    assert result["error"] is not None
    assert result["findings"] is None


# ---------------------------------------------------------------------------
# 2. KU not found → failed
# ---------------------------------------------------------------------------


def test_ku_not_found_fails():
    backend = MockBackend()  # empty
    result = verify_knowledge(
        _config(backend),
        {"ku_id": "KU-MISSING", "verification_data": {"type": "manual", "verdict": "confirmed"}},
    )
    assert result["status"] == "failed"
    assert "not found" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# 3. CMI verification upgrades grade when significant
# ---------------------------------------------------------------------------


def test_cmi_upgrades_grade_when_significant():
    import numpy as np

    rng = np.random.default_rng(42)
    treatment = list(rng.normal(10.0, 1.0, 100))
    control = list(rng.normal(0.0, 1.0, 100))

    backend = MockBackend({"KU-001": _make_ku("KU-001", "unverified")})
    result = verify_knowledge(
        _config(backend),
        {
            "ku_id": "KU-001",
            "verification_data": {"type": "cmi", "treatment": treatment, "control": control},
        },
    )
    assert result["status"] == "completed"
    findings = result["findings"]
    assert findings["old_grade"] == "unverified"
    assert findings["new_grade"] != "unverified"
    assert findings["action"] == "upgraded"


# ---------------------------------------------------------------------------
# 4. CMI no change when not significant
# ---------------------------------------------------------------------------


def test_cmi_no_change_when_not_significant():
    import numpy as np

    rng = np.random.default_rng(7)
    data = list(rng.normal(5.0, 2.0, 200))

    backend = MockBackend({"KU-001": _make_ku("KU-001", "low")})
    result = verify_knowledge(
        _config(backend),
        {
            "ku_id": "KU-001",
            "verification_data": {"type": "cmi", "treatment": data, "control": data},
        },
    )
    assert result["status"] == "completed"
    findings = result["findings"]
    assert findings["action"] == "no_change"
    assert findings["new_grade"] == findings["old_grade"]


# ---------------------------------------------------------------------------
# 5. Backtest upgrades on good sharpe
# ---------------------------------------------------------------------------


def test_backtest_upgrades_on_good_sharpe():
    # returns that give sharpe > 1
    returns = [0.005, 0.006, 0.004, 0.007, 0.005] * 50  # consistent positive returns

    backend = MockBackend({"KU-001": _make_ku("KU-001", "unverified")})
    result = verify_knowledge(
        _config(backend),
        {
            "ku_id": "KU-001",
            "verification_data": {"type": "backtest", "returns": returns},
        },
    )
    assert result["status"] == "completed"
    findings = result["findings"]
    assert findings["action"] == "upgraded"
    assert findings["new_grade"] != "unverified"


# ---------------------------------------------------------------------------
# 6. Backtest downgrades on negative sharpe
# ---------------------------------------------------------------------------


def test_backtest_downgrades_on_negative_sharpe():
    # returns that give negative sharpe
    returns = [-0.005, -0.003, -0.007, -0.004, -0.006] * 50

    backend = MockBackend({"KU-001": _make_ku("KU-001", "moderate")})
    result = verify_knowledge(
        _config(backend),
        {
            "ku_id": "KU-001",
            "verification_data": {"type": "backtest", "returns": returns},
        },
    )
    assert result["status"] == "completed"
    findings = result["findings"]
    assert findings["action"] == "downgraded"
    assert findings["new_grade"] != "moderate"


# ---------------------------------------------------------------------------
# 7. Manual confirmed upgrades
# ---------------------------------------------------------------------------


def test_manual_confirmed_upgrades():
    backend = MockBackend({"KU-001": _make_ku("KU-001", "low")})
    result = verify_knowledge(
        _config(backend),
        {
            "ku_id": "KU-001",
            "verification_data": {"type": "manual", "verdict": "confirmed"},
        },
    )
    assert result["status"] == "completed"
    findings = result["findings"]
    assert findings["action"] == "upgraded"
    assert findings["new_grade"] == "moderate"


# ---------------------------------------------------------------------------
# 8. Manual refuted downgrades
# ---------------------------------------------------------------------------


def test_manual_refuted_downgrades():
    backend = MockBackend({"KU-001": _make_ku("KU-001", "moderate")})
    result = verify_knowledge(
        _config(backend),
        {
            "ku_id": "KU-001",
            "verification_data": {"type": "manual", "verdict": "refuted"},
        },
    )
    assert result["status"] == "completed"
    findings = result["findings"]
    assert findings["action"] == "downgraded"
    assert findings["new_grade"] == "low"


# ---------------------------------------------------------------------------
# 9. Grade caps at "high" (not "proven" from auto-verify)
# ---------------------------------------------------------------------------


def test_grade_caps_at_high():
    # Start at "high" — upgrade should be capped
    backend = MockBackend({"KU-001": _make_ku("KU-001", "high")})
    result = verify_knowledge(
        _config(backend),
        {
            "ku_id": "KU-001",
            "verification_data": {"type": "manual", "verdict": "confirmed"},
        },
    )
    assert result["status"] == "completed"
    findings = result["findings"]
    assert findings["new_grade"] == "high"
    assert findings["action"] == "capped"


# ---------------------------------------------------------------------------
# 10. decision_trail returned and non-empty
# ---------------------------------------------------------------------------


def test_decision_trail_returned():
    backend = MockBackend({"KU-001": _make_ku("KU-001", "unverified")})
    result = verify_knowledge(
        _config(backend),
        {
            "ku_id": "KU-001",
            "verification_data": {"type": "manual", "verdict": "confirmed"},
        },
    )
    trail = result["decision_trail"]
    assert trail["omodul"] == "verify_knowledge"
    assert len(trail["steps"]) > 0


# ---------------------------------------------------------------------------
# 11. Standard return dict keys present
# ---------------------------------------------------------------------------


def test_standard_return_keys():
    backend = MockBackend({"KU-001": _make_ku("KU-001", "unverified")})
    result = verify_knowledge(
        _config(backend),
        {
            "ku_id": "KU-001",
            "verification_data": {"type": "manual", "verdict": "confirmed"},
        },
    )
    for key in ("findings", "status", "error", "decision_trail", "report_path", "cost_usd"):
        assert key in result


# ---------------------------------------------------------------------------
# 12. on_step callback accepted
# ---------------------------------------------------------------------------


def test_on_step_callback():
    steps = []
    backend = MockBackend({"KU-001": _make_ku("KU-001", "unverified")})
    verify_knowledge(
        _config(backend),
        {
            "ku_id": "KU-001",
            "verification_data": {"type": "manual", "verdict": "confirmed"},
        },
        on_step=steps.append,
    )
    assert len(steps) > 0
