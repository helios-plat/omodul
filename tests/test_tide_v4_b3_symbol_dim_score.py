"""Tests for omodul.symbol_dim_score (Tide v4 B3)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from omodul.symbol_dim_score import (
    SymbolDimScoreConfig,
    SymbolDimScoreInput,
    compute_fingerprint_for,
    symbol_dim_score,
)

_SYMBOL = "600519.SH"
_DATE = date(2024, 1, 15)
_ALL_DIMS = {
    "technical",
    "fundamentals",
    "valuation",
    "sentiment",
    "risk",
    "liquidity",
    "policy",
    "pattern",
}


def _ohlcv(n: int = 30) -> dict:
    rng = np.random.default_rng(42)
    close = (100.0 + np.cumsum(rng.normal(0, 1, n))).clip(10.0).tolist()
    high = [c + float(rng.uniform(0.5, 2.0)) for c in close]
    low = [c - float(rng.uniform(0.5, 2.0)) for c in close]
    return {
        "open": [c + float(rng.normal(0, 0.3)) for c in close],
        "high": high,
        "low": low,
        "close": close,
        "volume": [float(rng.uniform(1e6, 5e6)) for _ in range(n)],
    }


def _financials() -> dict:
    return {
        "net_profit": 10.0,
        "revenue": 100.0,
        "total_assets": 500.0,
        "total_liabilities": 200.0,
        "operating_cash_flow": 8.0,
        "total_equity": 300.0,
    }


def _valuation() -> dict:
    return {
        "current_price": 15.0,
        "shares_outstanding": 100.0,
        "free_cash_flows": [10.0, 11.0, 12.0, 13.0, 14.0],
    }


def _config(symbol: str = _SYMBOL) -> SymbolDimScoreConfig:
    return SymbolDimScoreConfig(symbol=symbol, trade_date=_DATE)


def _full_input() -> SymbolDimScoreInput:
    return SymbolDimScoreInput(
        ohlcv=_ohlcv(),
        financials=_financials(),
        valuation=_valuation(),
        news=[{"content": "营业收入增长 50 亿，利好。"}, {"content": "降准政策落地，利好市场。"}],
    )


# ── 1. 8 dims always present ──────────────────────────────────────────────────


def test_all_8_dims_present():
    result = symbol_dim_score(_config(), _full_input())
    assert set(result["scores"].keys()) == _ALL_DIMS
    assert set(result["evidence"].keys()) == _ALL_DIMS


# ── 2. scores in [0, 100] ─────────────────────────────────────────────────────


def test_scores_in_valid_range():
    result = symbol_dim_score(_config(), _full_input())
    for dim, score in result["scores"].items():
        assert 0.0 <= score <= 100.0, f"{dim} score {score} out of range"


# ── 3. empty input → all dims fallback to 50 ─────────────────────────────────


def test_empty_input_all_dims_fallback():
    inp = SymbolDimScoreInput()
    result = symbol_dim_score(_config(), inp)
    for dim, score in result["scores"].items():
        assert score == 50.0


# ── 4. missing financials → those dims fallback ───────────────────────────────


def test_missing_financials_dims_fallback():
    inp = SymbolDimScoreInput(ohlcv=_ohlcv())
    result = symbol_dim_score(_config(), inp)
    assert result["scores"]["fundamentals"] == 50.0
    assert result["scores"]["valuation"] == 50.0


# ── 5. fingerprint changes with symbol ───────────────────────────────────────


def test_fingerprint_changes_with_symbol():
    fp1 = compute_fingerprint_for(_config("600519.SH"), None)
    fp2 = compute_fingerprint_for(_config("000001.SZ"), None)
    assert fp1 != fp2


# ── 6. fingerprint stable for same symbol + date ─────────────────────────────


def test_fingerprint_stable_for_same_key():
    assert compute_fingerprint_for(_config(), None) == compute_fingerprint_for(_config(), None)


# ── 7. fingerprint ignores non-whitelist fields (different input_data) ────────


def test_fingerprint_ignores_non_whitelist_fields():
    fp1 = symbol_dim_score(_config(), SymbolDimScoreInput())["fingerprint"]
    fp2 = symbol_dim_score(_config(), _full_input())["fingerprint"]
    assert fp1 == fp2


# ── 8. compute_fingerprint_for returns 64-char hex ───────────────────────────


def test_compute_fingerprint_for_returns_hex():
    fp = compute_fingerprint_for(_config(), None)
    assert isinstance(fp, str) and len(fp) == 64
    int(fp, 16)


# ── 9. decision_trail is a dict with required keys ───────────────────────────


def test_decision_trail_has_required_fields():
    result = symbol_dim_score(_config(), _full_input())
    trail = result["decision_trail"]
    assert isinstance(trail, dict)
    assert trail["omodul_name"] == "symbol_dim_score"
    assert trail["status"] == "completed"


# ── 10. trail steps contain 8 oprim_batch entries ────────────────────────────


def test_trail_has_8_steps():
    result = symbol_dim_score(_config(), _full_input())
    steps = result["decision_trail"]["steps"]
    assert len(steps) == 8
    layers = {s["layer"] for s in steps}
    assert "oprim_batch" in layers


# ── 11. on_step callback receives step dicts ─────────────────────────────────


def test_on_step_callback_called_8_times():
    called: list[str] = []

    def cb(step: dict) -> None:
        called.append(step["callable"])

    symbol_dim_score(_config(), _full_input(), on_step=cb)
    assert len(called) == 8


# ── 12. bad input never raises ───────────────────────────────────────────────


def test_bad_input_never_raises():
    result = symbol_dim_score(_config(), SymbolDimScoreInput(ohlcv={"close": [1.0]}))
    assert "scores" in result
    assert result["status"] in ("completed", "failed")
