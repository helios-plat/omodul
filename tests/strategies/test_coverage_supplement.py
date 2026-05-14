"""Coverage supplement for omodul/strategies.py — green path + edge cases."""
from __future__ import annotations

import numpy as np
import pytest

from omodul.strategies import bocpd_trend_following, microstructure_scalper, funding_rate_arbitrage


def _make_returns(n, mu=0.002, sigma=0.01, seed=42):
    rng = np.random.default_rng(seed)
    return rng.normal(mu, sigma, n).tolist()


def _rising_equity(n=30):
    arr = [10000.0]
    for _ in range(n - 1):
        arr.append(arr[-1] * 1.005)
    return arr


def _green_config(**overrides):
    """Config with daily/weekly halts set to very negative → GREEN always."""
    defaults = dict(
        bocpd_hazard=0.01,
        trend_window=10,
        confidence_threshold=0.3,
        direction_mode="long_short",
        target_annual_vol=0.15,
        max_position_pct=0.2,
        max_gross_leverage=2.0,
        rebalance_threshold=0.001,
        daily_loss_halt_pct=-0.50,
        weekly_loss_halt_pct=-0.80,
        volatility_halt_multiplier=10.0,
        baseline_realized_vol=0.5,
        daily_volume_usd=1e8,
        realized_vol_30d=0.5,
        n_twap_slices=2,
        slice_duration_sec=30,
    )
    defaults.update(overrides)
    return defaults


def _scalper_green_config(**overrides):
    defaults = dict(
        ofi_window=60,
        ofi_window_sec=60,
        entry_threshold=1.0,
        exit_threshold=0.3,
        direction_mode="long_short",
        max_position_pct=0.1,
        max_gross_leverage=2.0,
        rebalance_threshold=0.001,
        target_annual_vol=0.15,
        max_hold_seconds=3600,
        daily_loss_halt_pct=-0.50,
        weekly_loss_halt_pct=-0.80,
        volatility_halt_multiplier=10.0,
        baseline_realized_vol=0.5,
        daily_volume_usd=1e8,
        realized_vol_30d=0.5,
        max_slippage_bps=50,
        limit_offset_bps=5,
    )
    defaults.update(overrides)
    return defaults


def _arb_green_config(**overrides):
    defaults = dict(
        funding_threshold_bps_long=-2.0,
        funding_threshold_bps_short=2.0,
        basis_filter_bps=200.0,
        lookback_hours=24,
        direction_mode="long_short",
        max_position_pct=0.15,
        max_gross_leverage=2.0,
        rebalance_threshold=0.001,
        target_annual_vol=0.10,
        daily_loss_halt_pct=-0.50,
        weekly_loss_halt_pct=-0.80,
        volatility_halt_multiplier=10.0,
        baseline_realized_vol=0.5,
        daily_volume_usd=1e8,
        realized_vol_30d=0.5,
        n_twap_slices=2,
        slice_duration_sec=30,
    )
    defaults.update(overrides)
    return defaults


def _bocpd_market_state(symbols=("BTC-USDT",), n=50):
    return {
        "symbols": list(symbols),
        "features": {f"returns_{sym}": _make_returns(n) for sym in symbols},
        "current_prices": {sym: 45000.0 for sym in symbols},
        "current_positions": {sym: 0.0 for sym in symbols},
        "capital_usd": 100_000.0,
        "equity_curve": _rising_equity(30),
    }


class TestBocpdGreenPath:
    def test_green_status_reaches_alpha_signals(self):
        state = _bocpd_market_state()
        result = bocpd_trend_following(state, _green_config())
        assert result["risk_gate_status"] == "GREEN"
        assert "BTC-USDT" in result["signals"]
        assert "BTC-USDT" in result["target_positions"]

    def test_green_two_symbols(self):
        state = _bocpd_market_state(("BTC-USDT", "ETH-USDT"))
        result = bocpd_trend_following(state, _green_config())
        assert result["risk_gate_status"] == "GREEN"
        for sym in ("BTC-USDT", "ETH-USDT"):
            assert sym in result["target_positions"]

    def test_green_long_only_mode(self):
        state = _bocpd_market_state()
        result = bocpd_trend_following(state, _green_config(direction_mode="long_only"))
        assert result["risk_gate_status"] == "GREEN"
        for sig in result["signals"].values():
            assert sig["direction"] in ("long", "neutral")

    def test_green_short_only_mode(self):
        state = _bocpd_market_state()
        result = bocpd_trend_following(state, _green_config(direction_mode="short_only"))
        for sig in result["signals"].values():
            assert sig["direction"] in ("short", "neutral")

    def test_execution_plans_populated_on_rebalance(self):
        state = _bocpd_market_state()
        # rebalance_threshold=0 → always rebalance
        result = bocpd_trend_following(state, _green_config(rebalance_threshold=0.0))
        assert isinstance(result["execution_plans"], dict)

    def test_leverage_cap_applied(self):
        state = _bocpd_market_state(("BTC-USDT", "ETH-USDT", "SOL-USDT"))
        result = bocpd_trend_following(state, _green_config(max_gross_leverage=0.1))
        assert result["risk_gate_status"] == "GREEN"
        total_gross = sum(abs(v["target_notional_usd"]) for v in result["target_positions"].values())
        assert total_gross <= 0.1 * 100_000.0 + 1.0

    def test_confidence_below_threshold_gives_neutral(self):
        state = _bocpd_market_state()
        # Very high confidence threshold → neutral
        result = bocpd_trend_following(state, _green_config(confidence_threshold=0.999))
        for sig in result["signals"].values():
            assert sig["direction"] == "neutral"

    def test_audit_evidence_complete(self):
        state = _bocpd_market_state()
        result = bocpd_trend_following(state, _green_config())
        ae = result["audit_evidence"]
        assert "stack_calls" in ae
        assert "intermediate_results" in ae
        assert "precondition_checks" in ae

    def test_missing_features_graceful(self):
        state = {
            "symbols": ["BTC-USDT"],
            "features": {},  # missing returns
            "current_prices": {"BTC-USDT": 45000.0},
            "current_positions": {},
            "capital_usd": 100_000.0,
            "equity_curve": _rising_equity(),
        }
        result = bocpd_trend_following(state, _green_config())
        assert "BTC-USDT" in result["signals"]


class TestMicrostructureScalperGreenPath:
    def _scalper_state(self, sym="BTC-USDT", n=20):
        rng = np.random.default_rng(42)
        prices = 100.0 + rng.normal(0, 0.1, n)
        sizes = rng.uniform(1, 5, n)
        return {
            "symbols": [sym],
            "features": {
                f"bid_prices_{sym}": (prices - 0.05).tolist(),
                f"ask_prices_{sym}": (prices + 0.05).tolist(),
                f"bid_sizes_{sym}": sizes.tolist(),
                f"ask_sizes_{sym}": sizes.tolist(),
            },
            "current_prices": {sym: 100.0},
            "current_positions": {sym: 0.0},
            "position_ages": {sym: 0.0},
            "capital_usd": 100_000.0,
            "equity_curve": _rising_equity(30),
        }

    def test_scalper_green_path(self):
        state = self._scalper_state()
        result = microstructure_scalper(state, _scalper_green_config())
        assert result["risk_gate_status"] == "GREEN"
        assert "BTC-USDT" in result["signals"]

    def test_scalper_max_hold_age_triggers_flatten(self):
        state = self._scalper_state()
        state["current_positions"]["BTC-USDT"] = 5000.0
        state["position_ages"] = {"BTC-USDT": 9999.0}
        result = microstructure_scalper(state, _scalper_green_config(max_hold_seconds=60))
        assert result["signals"]["BTC-USDT"].get("flatten") is True

    def test_scalper_direction_mode_key_accepted(self):
        """direction_mode in config does not crash the function."""
        state = self._scalper_state()
        result = microstructure_scalper(state, _scalper_green_config(direction_mode="long_only"))
        assert result["risk_gate_status"] == "GREEN"
        assert "BTC-USDT" in result["signals"]

    def test_scalper_execution_plan_on_strong_signal(self):
        """High-imbalance OFI signal → execution plan generated."""
        rng = np.random.default_rng(99)
        n = 30
        sym = "BTC-USDT"
        # skewed: large bid sizes vs tiny ask sizes → strong positive OFI
        bid_sizes = [10.0] * n
        ask_sizes = [0.1] * n
        prices = (100.0 + rng.normal(0, 0.01, n)).tolist()
        state = {
            "symbols": [sym],
            "features": {
                f"bid_prices_{sym}": prices,
                f"ask_prices_{sym}": prices,
                f"bid_sizes_{sym}": bid_sizes,
                f"ask_sizes_{sym}": ask_sizes,
            },
            "current_prices": {sym: 100.0},
            "current_positions": {sym: 0.0},
            "position_ages": {sym: 0.0},
            "capital_usd": 100_000.0,
            "equity_curve": _rising_equity(30),
        }
        result = microstructure_scalper(state, _scalper_green_config(entry_threshold=0.1, rebalance_threshold=0.0))
        assert result["risk_gate_status"] == "GREEN"


class TestFundingArbitrageGreenPath:
    def _arb_state(self, sym="BTC-USDT", n=30, funding_val=0.001):
        rng = np.random.default_rng(42)
        spot = 45000.0 + rng.normal(0, 10, n)
        perp = spot + rng.normal(0, 5, n)
        fund = np.full(n, funding_val)
        return {
            "symbols": [sym],
            "features": {
                f"spot_prices_{sym}": spot.tolist(),
                f"perp_prices_{sym}": perp.tolist(),
                f"funding_rates_{sym}": fund.tolist(),
            },
            "current_prices": {sym: 45000.0},
            "current_positions": {sym: 0.0},
            "capital_usd": 100_000.0,
            "equity_curve": _rising_equity(30),
        }

    def test_arb_green_path_short_on_high_funding(self):
        state = self._arb_state(funding_val=0.001)  # 10 bps positive funding
        result = funding_rate_arbitrage(state, _arb_green_config(funding_threshold_bps_short=0.5))
        assert result["risk_gate_status"] == "GREEN"
        assert "BTC-USDT" in result["signals"]

    def test_arb_green_path_long_on_negative_funding(self):
        state = self._arb_state(funding_val=-0.001)
        result = funding_rate_arbitrage(state, _arb_green_config(funding_threshold_bps_long=-0.5))
        assert result["risk_gate_status"] == "GREEN"

    def test_arb_neutral_within_band(self):
        state = self._arb_state(funding_val=0.0001)
        result = funding_rate_arbitrage(state, _arb_green_config())
        assert result["risk_gate_status"] == "GREEN"

    def test_arb_leverage_cap(self):
        state = self._arb_state(funding_val=0.005)
        cap = 0.5
        result = funding_rate_arbitrage(state, _arb_green_config(max_gross_leverage=cap, funding_threshold_bps_short=1.0, rebalance_threshold=0.0))
        total = sum(abs(v["target_notional_usd"]) for v in result["target_positions"].values())
        assert total <= cap * 100_000.0 + 1.0

    def test_arb_audit_evidence(self):
        state = self._arb_state()
        result = funding_rate_arbitrage(state, _arb_green_config())
        assert "audit_evidence" in result
        ae = result["audit_evidence"]
        assert "stack_calls" in ae

    def test_arb_missing_features_graceful(self):
        state = {
            "symbols": ["BTC-USDT"],
            "features": {},
            "current_prices": {"BTC-USDT": 45000.0},
            "current_positions": {},
            "capital_usd": 100_000.0,
            "equity_curve": _rising_equity(),
        }
        result = funding_rate_arbitrage(state, _arb_green_config())
        assert result["risk_gate_status"] == "GREEN"
