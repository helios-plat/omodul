"""Tests for omodul.strategies."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from omodul.strategies import (
    bocpd_trend_following,
    microstructure_scalper,
    funding_rate_arbitrage,
)

REAL_DATA_DIR = Path(__file__).parent / "real_data"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_returns(n=100, mean=0.001, std=0.003, seed=42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(mean, std, n)


def _equity_curve(n=20, start=10000.0, drift=0.001) -> list:
    arr = [start]
    for _ in range(n - 1):
        arr.append(arr[-1] * (1 + drift))
    return arr


def _equity_curve_red(start=10000.0) -> list:
    """Equity curve where arr[-5] is high and arr[-1] is 25% lower → RED.

    weekly_loss = (arr[-1] - arr[n-5]) / arr[n-5], so we need arr[n-5] high.
    With n=10: weekly_start=5; arr[5]=start, arr[9]=start*0.75.
    """
    # First 5 flat, then 5-bar drop of -25%
    curve = [start] * 4 + [start, start * 0.95, start * 0.90, start * 0.85, start * 0.80, start * 0.75]
    return curve


def _bocpd_config(**overrides) -> dict:
    defaults = dict(
        bocpd_hazard=0.01,
        trend_window=10,
        confidence_threshold=0.3,
        direction_mode="long_short",
        target_annual_vol=0.15,
        max_position_pct=0.2,
        max_gross_leverage=2.0,
        rebalance_threshold=0.001,
        daily_loss_halt_pct=0.05,
        weekly_loss_halt_pct=0.15,
        volatility_halt_multiplier=2.0,
        baseline_realized_vol=0.5,
        daily_volume_usd=1e8,
        realized_vol_30d=0.5,
        n_twap_slices=3,
        slice_duration_sec=30,
    )
    defaults.update(overrides)
    return defaults


def _scalper_config(**overrides) -> dict:
    defaults = dict(
        ofi_window=60,
        entry_threshold=1.0,
        exit_threshold=0.5,
        max_hold_seconds=300,
        max_position_pct=0.1,
        limit_offset_bps=5,
        max_slippage_bps=50,
        timeout_sec=30,
        on_timeout="cancel",
        daily_loss_halt_pct=0.05,
        weekly_loss_halt_pct=0.15,
        volatility_halt_multiplier=2.0,
        baseline_realized_vol=0.5,
        daily_volume_usd=1e8,
        realized_vol_30d=0.5,
    )
    defaults.update(overrides)
    return defaults


def _funding_config(**overrides) -> dict:
    defaults = dict(
        max_leverage=2.0,
        funding_threshold_bps_long=5.0,
        funding_threshold_bps_short=10.0,
        basis_filter_bps=50.0,
        lookback_hours=8,
        target_annual_vol=0.15,
        max_position_pct=0.2,
        rebalance_threshold=0.001,
        daily_loss_halt_pct=0.05,
        weekly_loss_halt_pct=0.15,
        volatility_halt_multiplier=2.0,
        baseline_realized_vol=0.5,
        daily_volume_usd=1e8,
        realized_vol_30d=0.5,
        n_twap_slices=3,
        slice_duration_sec=30,
    )
    defaults.update(overrides)
    return defaults


def _required_decision_keys() -> set:
    return {"signals", "target_positions", "risk_gate_status", "execution_plans", "audit_evidence"}


# ─── BOCPD Trend Following ───────────────────────────────────────────────────

class TestBocpdTrendFollowing:
    @pytest.mark.academic_reference
    def test_bocpd_e2e(self):
        """Full pipeline with valid market_state → valid StrategyDecision."""
        symbols = ["BTC-USDT", "ETH-USDT"]
        n = 80
        market_state = {
            "symbols": symbols,
            "features": {
                "returns_BTC-USDT": _make_returns(n, 0.002, 0.003, 42),
                "returns_ETH-USDT": _make_returns(n, 0.001, 0.004, 99),
            },
            "current_prices": {"BTC-USDT": 45000.0, "ETH-USDT": 3000.0},
            "current_positions": {"BTC-USDT": 0.0, "ETH-USDT": 0.0},
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve(20),
        }
        result = bocpd_trend_following(market_state, _bocpd_config())
        assert _required_decision_keys().issubset(set(result.keys()))
        assert result["risk_gate_status"] in {"GREEN", "YELLOW", "ORANGE", "RED"}
        for sym in symbols:
            assert sym in result["signals"]
            assert sym in result["target_positions"]

    def test_bocpd_red_gate_zeroes_positions(self):
        """RED equity curve → all target_positions have target_notional=0."""
        symbols = ["BTC-USDT"]
        market_state = {
            "symbols": symbols,
            "features": {"returns_BTC-USDT": _make_returns(50, 0.002, 0.003, 42)},
            "current_prices": {"BTC-USDT": 45000.0},
            "current_positions": {"BTC-USDT": 5000.0},
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve_red(),
        }
        result = bocpd_trend_following(
            market_state,
            _bocpd_config(weekly_loss_halt_pct=0.10),
        )
        assert result["risk_gate_status"] == "RED"
        for sym in symbols:
            assert result["target_positions"][sym]["target_notional_usd"] == 0.0

    def test_bocpd_audit_evidence_complete(self):
        """audit_evidence must have stack_calls, intermediate_results, precondition_checks."""
        symbols = ["BTC-USDT"]
        market_state = {
            "symbols": symbols,
            "features": {"returns_BTC-USDT": _make_returns(50)},
            "current_prices": {"BTC-USDT": 45000.0},
            "current_positions": {},
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve(20),
        }
        result = bocpd_trend_following(market_state, _bocpd_config())
        ae = result["audit_evidence"]
        assert isinstance(ae["stack_calls"], list)
        assert isinstance(ae["intermediate_results"], dict)
        assert isinstance(ae["precondition_checks"], list)

    def test_bocpd_gross_leverage_respected(self):
        """Total gross exposure must not exceed max_gross_leverage * capital."""
        symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
        n = 60
        market_state = {
            "symbols": symbols,
            "features": {
                "returns_BTC-USDT": _make_returns(n, 0.005, 0.001, 1),
                "returns_ETH-USDT": _make_returns(n, 0.005, 0.001, 2),
                "returns_SOL-USDT": _make_returns(n, 0.005, 0.001, 3),
            },
            "current_prices": {"BTC-USDT": 45000.0, "ETH-USDT": 3000.0, "SOL-USDT": 100.0},
            "current_positions": {s: 0.0 for s in symbols},
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve(20),
        }
        config = _bocpd_config(
            max_gross_leverage=1.5,
            confidence_threshold=0.0,  # force signals
        )
        result = bocpd_trend_following(market_state, config)
        total_gross = sum(
            abs(p["target_notional_usd"])
            for p in result["target_positions"].values()
        )
        assert total_gross <= 1.5 * 100_000.0 + 1.0  # 1 USD float tolerance


# ─── Microstructure Scalper ──────────────────────────────────────────────────

def _make_ob_features(sym, n=100, buy_pressure=True):
    prices = np.linspace(45000, 45100, n)
    if buy_pressure:
        bid_s = np.full(n, 10.0)
        ask_s = np.full(n, 1.0)
    else:
        bid_s = np.ones(n)
        ask_s = np.ones(n)
    return {
        f"bid_prices_{sym}": prices,
        f"ask_prices_{sym}": prices + 1,
        f"bid_sizes_{sym}": bid_s,
        f"ask_sizes_{sym}": ask_s,
    }


class TestMicrostructureScalper:
    @pytest.mark.academic_reference
    def test_scalper_e2e(self):
        symbols = ["BTC-USDT"]
        features = _make_ob_features("BTC-USDT", 100)
        market_state = {
            "symbols": symbols,
            "features": features,
            "current_prices": {"BTC-USDT": 45050.0},
            "current_positions": {"BTC-USDT": 0.0},
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve(20),
        }
        result = microstructure_scalper(market_state, _scalper_config())
        assert _required_decision_keys().issubset(set(result.keys()))
        assert "BTC-USDT" in result["signals"]
        assert "BTC-USDT" in result["target_positions"]

    def test_scalper_max_hold_forces_flatten(self):
        """Old position (age > max_hold_seconds) → target_notional = 0."""
        symbols = ["BTC-USDT"]
        features = _make_ob_features("BTC-USDT", 100)
        market_state = {
            "symbols": symbols,
            "features": features,
            "current_prices": {"BTC-USDT": 45050.0},
            "current_positions": {"BTC-USDT": 5000.0},  # has a position
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve(20),
            "position_ages": {"BTC-USDT": 999},  # far exceeds max_hold_seconds=300
        }
        result = microstructure_scalper(
            market_state,
            _scalper_config(max_hold_seconds=300),
        )
        assert result["target_positions"]["BTC-USDT"]["target_notional_usd"] == 0.0

    def test_scalper_audit_evidence_complete(self):
        symbols = ["BTC-USDT"]
        features = _make_ob_features("BTC-USDT")
        market_state = {
            "symbols": symbols,
            "features": features,
            "current_positions": {},
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve(20),
        }
        result = microstructure_scalper(market_state, _scalper_config())
        ae = result["audit_evidence"]
        assert isinstance(ae["stack_calls"], list)
        assert isinstance(ae["intermediate_results"], dict)
        assert isinstance(ae["precondition_checks"], list)

    def test_scalper_red_gate(self):
        symbols = ["BTC-USDT"]
        features = _make_ob_features("BTC-USDT")
        market_state = {
            "symbols": symbols,
            "features": features,
            "current_positions": {},
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve_red(),
        }
        result = microstructure_scalper(
            market_state,
            _scalper_config(weekly_loss_halt_pct=0.10),
        )
        assert result["risk_gate_status"] == "RED"
        assert result["target_positions"]["BTC-USDT"]["target_notional_usd"] == 0.0


# ─── Funding Rate Arbitrage ──────────────────────────────────────────────────

def _make_funding_features(sym, n=24, funding_level=-0.001):
    spot = np.full(n, 45000.0)
    perp = spot * (1 + 0.0001)
    fund = np.full(n, funding_level)
    return {
        f"spot_prices_{sym}": spot,
        f"perp_prices_{sym}": perp,
        f"funding_rates_{sym}": fund,
    }


class TestFundingRateArbitrage:
    @pytest.mark.academic_reference
    def test_funding_e2e(self):
        symbols = ["BTC-USDT"]
        features = _make_funding_features("BTC-USDT", 24, -0.001)
        market_state = {
            "symbols": symbols,
            "features": features,
            "current_prices": {"BTC-USDT": 45000.0},
            "current_positions": {"BTC-USDT": 0.0},
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve(20),
        }
        result = funding_rate_arbitrage(market_state, _funding_config())
        assert _required_decision_keys().issubset(set(result.keys()))
        assert "BTC-USDT" in result["signals"]

    def test_funding_leverage_cap(self):
        """Total gross exposure must not exceed max_leverage * capital."""
        symbols = ["BTC-USDT", "ETH-USDT"]
        features = {}
        for sym in symbols:
            features.update(_make_funding_features(sym, 24, -0.002))
        # Override eth price fields
        features["spot_prices_ETH-USDT"] = np.full(24, 3000.0)
        features["perp_prices_ETH-USDT"] = np.full(24, 3000.3)

        market_state = {
            "symbols": symbols,
            "features": features,
            "current_positions": {s: 0.0 for s in symbols},
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve(20),
        }
        config = _funding_config(max_leverage=2.0)
        result = funding_rate_arbitrage(market_state, config)
        total_gross = sum(
            abs(p["target_notional_usd"])
            for p in result["target_positions"].values()
        )
        assert total_gross <= 2.0 * 100_000.0 + 1.0

    def test_funding_red_gate(self):
        symbols = ["BTC-USDT"]
        features = _make_funding_features("BTC-USDT", 24, -0.001)
        market_state = {
            "symbols": symbols,
            "features": features,
            "current_positions": {},
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve_red(),
        }
        result = funding_rate_arbitrage(
            market_state,
            _funding_config(weekly_loss_halt_pct=0.10),
        )
        assert result["risk_gate_status"] == "RED"
        assert result["target_positions"]["BTC-USDT"]["target_notional_usd"] == 0.0

    def test_funding_audit_evidence_complete(self):
        symbols = ["BTC-USDT"]
        features = _make_funding_features("BTC-USDT")
        market_state = {
            "symbols": symbols,
            "features": features,
            "current_positions": {},
            "capital_usd": 100_000.0,
            "equity_curve": _equity_curve(20),
        }
        result = funding_rate_arbitrage(market_state, _funding_config())
        ae = result["audit_evidence"]
        assert isinstance(ae["stack_calls"], list)
        assert isinstance(ae["intermediate_results"], dict)
        assert isinstance(ae["precondition_checks"], list)
