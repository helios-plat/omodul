"""Tests for omodul.portfolio.cvar_risk_workflow (4-pillar omodul)."""

import numpy as np
import pandas as pd

from omodul.portfolio.cvar_risk_workflow import CvarRiskConfig, cvar_risk_workflow


def _synthetic_returns(n_assets=3, n_obs=300, seed=42):
    rng = np.random.default_rng(seed)
    cols = [f"SYM{i}" for i in range(n_assets)]
    data = rng.normal(0.0003, 0.01, size=(n_obs, n_assets))
    return pd.DataFrame(data, columns=cols)


def _base_input():
    returns = _synthetic_returns()
    return {
        "returns": returns,
        "current_positions_usd": {s: 0.0 for s in returns.columns},
        "capital_usd": 10_000.0,
        "atr_pct": {s: 0.02 for s in returns.columns},
    }


def test_completed_status_and_findings_shape(tmp_path):
    config = CvarRiskConfig(symbols=["SYM0", "SYM1", "SYM2"])
    result = cvar_risk_workflow(config, _base_input(), tmp_path)
    assert result["status"] == "completed"
    assert result["error"] is None
    assert "weights" in result["findings"]
    assert "position_caps" in result["findings"]
    assert set(result["findings"]["position_caps"].keys()) == {"SYM0", "SYM1", "SYM2"}


def test_fingerprint_deterministic(tmp_path):
    config = CvarRiskConfig(symbols=["SYM0", "SYM1", "SYM2"])
    input_data = _base_input()
    r1 = cvar_risk_workflow(config, input_data, tmp_path)
    r2 = cvar_risk_workflow(config, input_data, tmp_path)
    assert r1["fingerprint"] == r2["fingerprint"]
    assert len(r1["fingerprint"]) == 64


def test_decision_trail_has_three_steps(tmp_path):
    config = CvarRiskConfig()
    result = cvar_risk_workflow(config, _base_input(), tmp_path)
    assert len(result["decision_trail"]["steps"]) == 3


def test_report_written_to_output_dir(tmp_path):
    config = CvarRiskConfig()
    result = cvar_risk_workflow(config, _base_input(), tmp_path)
    assert result["report_path"] is not None
    assert result["report_path"].exists()


def test_circuit_breaker_absent_without_equity_curve(tmp_path):
    config = CvarRiskConfig()
    result = cvar_risk_workflow(config, _base_input(), tmp_path)
    assert result["findings"]["circuit_breaker"] is None


def test_circuit_breaker_runs_when_equity_curve_present(tmp_path):
    config = CvarRiskConfig()
    input_data = _base_input()
    input_data["equity_curve"] = [10_000, 10_200, 9_500, 9_000]
    result = cvar_risk_workflow(config, input_data, tmp_path)
    cb = result["findings"]["circuit_breaker"]
    assert cb is not None
    assert cb["status"] in {"GREEN", "YELLOW", "ORANGE", "RED"}


def test_equal_weight_fallback_propagates_into_sizing(tmp_path):
    config = CvarRiskConfig(min_obs=1000)  # force fallback: not enough obs
    result = cvar_risk_workflow(config, _base_input(), tmp_path)
    assert result["findings"]["weights"]["method"] == "equal_weight_fallback"
    caps = result["findings"]["position_caps"]
    assert len(caps) == 3


def test_failure_status_on_missing_required_key(tmp_path):
    config = CvarRiskConfig()
    result = cvar_risk_workflow(config, {"returns": _synthetic_returns()}, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "KeyError"
    assert result["findings"] is None
