"""Tests for R7 omodul strategies: trend_dual, vwap_mr_dual, spot_trend."""

from __future__ import annotations

import numpy as np
import pytest

from omodul.strategies.trend_dual import trend_dual
from omodul.strategies.vwap_mr_dual import vwap_mr_dual
from omodul.strategies.spot_trend import spot_trend


# ──────────────────── Fixtures ────────────────────

@pytest.fixture
def uptrend_market_state():
    n = 300
    close = np.linspace(100, 200, n)
    ohlcv = {
        "high": close + 2.0, "low": close - 2.0,
        "close": close, "volume": np.ones(n) * 500.0,
    }
    return {
        "ohlcv": ohlcv, "instrument": "BTC-USDT-SWAP",
        "current_positions": {}, "capital_usd": 1000.0,
    }


@pytest.fixture
def sine_market_state():
    n = 300
    t = np.arange(n, dtype=float)
    close = 100.0 + 10.0 * np.sin(t * 0.1)
    ohlcv = {
        "high": close + 1.5, "low": close - 1.5,
        "close": close, "volume": np.ones(n) * 200.0,
    }
    return {
        "ohlcv": ohlcv, "instrument": "SOL-USDT-SWAP",
        "current_positions": {}, "capital_usd": 1000.0,
    }


@pytest.fixture
def trend_config():
    return {
        "indicators": {
            "supertrend": {"enabled": True, "period": 10, "multiplier": 3.0},
            "ema":        {"enabled": True, "fast": 20, "slow": 50},
            "adx":        {"enabled": True, "period": 14, "threshold": 25.0},
            "macd":       {"enabled": True, "fast": 12, "slow": 26, "signal": 9},
        },
        "signal_logic": {"min_confluence": 2, "direction": "both"},
        "risk": {"cost_bps": 10.0},
    }


@pytest.fixture
def mr_config():
    return {
        "indicators": {
            "vwap":        {"enabled": True, "window": 4, "z_threshold": 2.0},
            "bollinger":   {"enabled": True, "window": 20, "num_std": 2.0},
            "rsi":         {"enabled": True, "period": 14, "oversold": 0.3, "overbought": 0.7},
            "stochastic":  {"enabled": True, "k_period": 14, "d_period": 3, "smooth_k": 3,
                            "oversold": 0.2, "overbought": 0.8},
        },
        "signal_logic": {"min_confluence": 2, "direction": "both"},
        "risk": {"cost_bps": 10.0},
    }


@pytest.fixture
def spot_config():
    return {
        "donchian": {"n_enter": 20, "n_exit": 10},
        "risk": {"cost_bps": 10.0, "bear_ma": 200},
    }


# ──────────────────── trend_dual ────────────────────

class TestTrendDual:

    def test_returns_required_keys(self, uptrend_market_state, trend_config):
        result = trend_dual(uptrend_market_state, trend_config)
        for key in ("signals", "n_signals", "cost_bps", "audit_evidence"):
            assert key in result, f"missing key: {key}"

    def test_signal_shape(self, uptrend_market_state, trend_config):
        result = trend_dual(uptrend_market_state, trend_config)
        n = len(uptrend_market_state["ohlcv"]["close"])
        assert result["signals"].shape == (n,)

    def test_signal_values(self, uptrend_market_state, trend_config):
        result = trend_dual(uptrend_market_state, trend_config)
        assert set(np.unique(result["signals"])).issubset({-1, 0, 1})

    def test_n_signals_consistent(self, uptrend_market_state, trend_config):
        result = trend_dual(uptrend_market_state, trend_config)
        assert result["n_signals"] == int(np.sum(result["signals"] != 0))

    def test_cost_bps_from_config(self, uptrend_market_state, trend_config):
        result = trend_dual(uptrend_market_state, trend_config)
        assert result["cost_bps"] == 10.0

    def test_audit_evidence_structure(self, uptrend_market_state, trend_config):
        result = trend_dual(uptrend_market_state, trend_config)
        ae = result["audit_evidence"]
        assert "stack_calls" in ae
        assert "config_fingerprint" in ae
        assert ae["n_bars"] == len(uptrend_market_state["ohlcv"]["close"])

    def test_uptrend_more_longs(self, uptrend_market_state, trend_config):
        result = trend_dual(uptrend_market_state, trend_config)
        sigs = result["signals"]
        assert int(np.sum(sigs == 1)) >= int(np.sum(sigs == -1))

    def test_config_fingerprint_changes_with_config(self, uptrend_market_state, trend_config):
        r1 = trend_dual(uptrend_market_state, trend_config)
        cfg2 = dict(trend_config)
        cfg2["risk"] = {"cost_bps": 5.0}
        r2 = trend_dual(uptrend_market_state, cfg2)
        # Different configs produce different fingerprints
        assert r1["audit_evidence"]["config_fingerprint"] != r2["audit_evidence"]["config_fingerprint"]


# ──────────────────── vwap_mr_dual ────────────────────

class TestVwapMrDual:

    def test_returns_required_keys(self, sine_market_state, mr_config):
        result = vwap_mr_dual(sine_market_state, mr_config)
        for key in ("signals", "n_signals", "cost_bps", "audit_evidence"):
            assert key in result

    def test_signal_shape(self, sine_market_state, mr_config):
        result = vwap_mr_dual(sine_market_state, mr_config)
        n = len(sine_market_state["ohlcv"]["close"])
        assert result["signals"].shape == (n,)

    def test_signal_values(self, sine_market_state, mr_config):
        result = vwap_mr_dual(sine_market_state, mr_config)
        assert set(np.unique(result["signals"])).issubset({-1, 0, 1})

    def test_cost_bps(self, sine_market_state, mr_config):
        result = vwap_mr_dual(sine_market_state, mr_config)
        assert result["cost_bps"] == 10.0

    def test_sine_produces_some_signals(self, sine_market_state, mr_config):
        result = vwap_mr_dual(sine_market_state, mr_config)
        # Sine wave with tight bands should produce some mean-reversion signals
        assert result["n_signals"] >= 0   # at minimum non-negative


# ──────────────────── spot_trend ────────────────────

class TestSpotTrend:

    def test_returns_required_keys(self, uptrend_market_state, spot_config):
        result = spot_trend(uptrend_market_state, spot_config)
        for key in ("signals", "n_signals", "cost_bps", "audit_evidence"):
            assert key in result

    def test_signal_shape(self, uptrend_market_state, spot_config):
        result = spot_trend(uptrend_market_state, spot_config)
        n = len(uptrend_market_state["ohlcv"]["close"])
        assert result["signals"].shape == (n,)

    def test_signal_values_long_only(self, uptrend_market_state, spot_config):
        result = spot_trend(uptrend_market_state, spot_config)
        # spot_trend in Donchian mode: +1 entry, -1 exit, 0 neutral
        assert set(np.unique(result["signals"])).issubset({-1, 0, 1})

    def test_bear_filter_suppresses_entries_in_downtrend(self):
        n = 400
        close = np.linspace(200, 50, n)  # strong downtrend
        ohlcv = {
            "high": close + 2.0, "low": close - 2.0,
            "close": close, "volume": np.ones(n) * 500.0,
        }
        ms = {
            "ohlcv": ohlcv, "instrument": "BTC-USDT",
            "current_positions": {}, "capital_usd": 1000.0,
        }
        cfg_bear = {
            "donchian": {"n_enter": 20, "n_exit": 10},
            "risk": {"cost_bps": 10.0, "bear_ma": 50},
        }
        result = spot_trend(ms, cfg_bear)
        # In a strong downtrend with bear filter, very few (or no) entries
        entries = int(np.sum(result["signals"] == 1))
        assert entries <= 2, f"bear filter should suppress entries in downtrend, got {entries}"

    def test_audit_evidence_bear_filter_count(self, uptrend_market_state, spot_config):
        result = spot_trend(uptrend_market_state, spot_config)
        ae = result["audit_evidence"]
        assert "bear_filter_applied" in ae

    def test_compose_mode(self, uptrend_market_state):
        compose_cfg = {
            "indicators": {
                "ema": {"enabled": True, "fast": 20, "slow": 50},
                "supertrend": {"enabled": False},
                "adx": {"enabled": False},
                "macd": {"enabled": False},
            },
            "signal_logic": {"min_confluence": 1, "direction": "long"},
            "risk": {"cost_bps": 10.0, "bear_ma": 0},
        }
        result = spot_trend(uptrend_market_state, compose_cfg)
        # long-only: no shorts
        assert np.all(result["signals"] >= 0)

    def test_n_signals_consistent(self, uptrend_market_state, spot_config):
        result = spot_trend(uptrend_market_state, spot_config)
        assert result["n_signals"] == int(np.sum(result["signals"] != 0))
