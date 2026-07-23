"""Tests for omodul.regime_workflow (4-pillar regime omodul)."""

import numpy as np

from omodul.regime_workflow import RegimeConfig, regime_workflow


def _series(n=800, seed=0, vol=0.01):
    rng = np.random.default_rng(seed)
    return list(100.0 * np.exp(np.cumsum(rng.normal(0, vol, n))))


def test_deterministic_completed(tmp_path):
    r = regime_workflow(RegimeConfig(method="deterministic"), {"closes": _series()}, tmp_path)
    assert r["status"] == "completed"
    assert r["findings"]["state"] in {"crisis", "trend", "range"}
    assert r["findings"]["advisory"] is True
    assert len(r["fingerprint"]) == 64


def test_hmm_falls_back_when_unavailable(tmp_path):
    # hmmlearn is not installed in this env -> workflow must fall back, not fail
    r = regime_workflow(RegimeConfig(method="hmm"), {"closes": _series()}, tmp_path)
    assert r["status"] == "completed"
    assert r["findings"]["method_used"] in {"hmm", "deterministic_fallback"}


def test_report_written(tmp_path):
    r = regime_workflow(RegimeConfig(method="deterministic"), {"closes": _series()}, tmp_path)
    assert r["report_path"] is not None and r["report_path"].exists()


def test_missing_closes_fails_gracefully(tmp_path):
    r = regime_workflow(RegimeConfig(), {}, tmp_path)
    assert r["status"] == "failed"
    assert r["error"]["type"] == "KeyError"


def test_decision_trail_records_method(tmp_path):
    r = regime_workflow(RegimeConfig(method="deterministic"), {"closes": _series()}, tmp_path)
    steps = r["decision_trail"]["steps"]
    assert len(steps) == 1
    assert "market_regime" in steps[0]["callable"]
