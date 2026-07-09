"""Tests for omodul.ml_signal_workflow + omodul.llm_signal_workflow."""

import numpy as np

from omodul.llm_signal_workflow import LlmSignalConfig, llm_signal_workflow
from omodul.ml_signal_workflow import MlSignalConfig, ml_signal_workflow


def _series(n=900, seed=0):
    rng = np.random.default_rng(seed)
    return list(100 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n))))


def test_ml_completes_and_gates(tmp_path):
    r = ml_signal_workflow(MlSignalConfig(symbol="BTC-USDT-SWAP"), {"closes": _series()}, tmp_path)
    assert r["status"] == "completed"
    f = r["findings"]
    # random-walk data must NOT be promoted (the gate has to fire)
    assert f["promoted"] is False
    assert 0.0 <= f["dsr"] <= 1.0
    assert "wfv_accuracy" in f and "oos_sharpe" in f
    assert len(r["fingerprint"]) == 64


def test_ml_thin_data_completes_unpromoted(tmp_path):
    # too few bars for any WFV fold -> completes with a neutral, un-promoted
    # signal (0 folds), never promoted on no evidence
    r = ml_signal_workflow(MlSignalConfig(), {"closes": _series(n=150)}, tmp_path)
    assert r["status"] == "completed"
    assert r["findings"]["promoted"] is False
    assert r["findings"]["n_folds"] == 0


def test_ml_missing_closes_fails(tmp_path):
    r = ml_signal_workflow(MlSignalConfig(), {}, tmp_path)
    assert r["status"] == "failed"
    assert r["error"]["type"] == "KeyError"


def test_llm_slot_disabled_zero_cost(tmp_path):
    r = llm_signal_workflow(LlmSignalConfig(symbol="BTC-USDT-SWAP"), {}, tmp_path)
    assert r["status"] == "completed"
    assert r["findings"]["enabled"] is False
    assert r["findings"]["direction"] == "neutral"
    assert r["findings"]["promoted"] is False
    assert r["cost_usd"] == 0.0


def test_llm_enabled_hook_unwired(tmp_path):
    # enabling without a wired provider must fail loudly, never silently bill
    r = llm_signal_workflow(LlmSignalConfig(enabled=True), {"context": "x"}, tmp_path)
    assert r["status"] == "failed"
    assert r["error"]["type"] == "NotImplementedError"
