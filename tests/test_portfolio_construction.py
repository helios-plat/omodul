"""Tests for omodul.portfolio_construction.vol_target."""
from __future__ import annotations

import pytest

from omodul.portfolio_construction import vol_target

_DEFAULT_VOLS = {"BTC-USDT": 0.8, "ETH-USDT": 0.9}


def _make_signals(
    btc_dir="long", btc_str=0.7,
    eth_dir="long", eth_str=0.5,
):
    return {
        "BTC-USDT": {"direction": btc_dir, "strength": btc_str, "confidence": 0.8},
        "ETH-USDT": {"direction": eth_dir, "strength": eth_str, "confidence": 0.7},
    }


class TestVolTargetBasic:
    def test_vol_target_basic(self):
        signals = _make_signals("long", 0.7, "long", 0.5)
        result = vol_target(
            signals=signals,
            current_positions={"BTC-USDT": 0.0, "ETH-USDT": 0.0},
            capital_usd=100_000.0,
            target_annual_vol=0.15,
            max_position_pct=0.3,
            max_gross_leverage=2.0,
            rebalance_threshold=0.001,
            instrument_vols=_DEFAULT_VOLS,
        )
        assert result["target_positions"]["BTC-USDT"]["target_notional_usd"] > 0
        assert result["target_positions"]["ETH-USDT"]["target_notional_usd"] > 0

    def test_vol_target_short_signal_negative_notional(self):
        signals = _make_signals("short", 0.6, "short", 0.4)
        result = vol_target(
            signals=signals,
            current_positions={},
            capital_usd=100_000.0,
            target_annual_vol=0.15,
            max_position_pct=0.3,
            max_gross_leverage=2.0,
            rebalance_threshold=0.001,
            instrument_vols=_DEFAULT_VOLS,
        )
        assert result["target_positions"]["BTC-USDT"]["target_notional_usd"] < 0
        assert result["target_positions"]["ETH-USDT"]["target_notional_usd"] < 0


class TestVolTargetLeverageCap:
    def test_vol_target_gross_leverage_cap(self):
        """Total gross must not exceed max_gross_leverage * capital_usd."""
        signals = _make_signals("long", 1.0, "long", 1.0)
        capital = 100_000.0
        max_lev = 1.5
        result = vol_target(
            signals=signals,
            current_positions={},
            capital_usd=capital,
            target_annual_vol=0.15,
            max_position_pct=0.3,
            max_gross_leverage=max_lev,
            rebalance_threshold=0.001,
            instrument_vols=_DEFAULT_VOLS,
        )
        total_gross = result["total_gross_exposure"]
        assert total_gross <= max_lev * capital + 1e-6  # tiny float tolerance


class TestVolTargetRebalance:
    def test_vol_target_rebalance_threshold(self):
        """Tiny position change below threshold → no rebalance (hold)."""
        signals = _make_signals("long", 0.5, "long", 0.5)
        # Set current positions very close to target
        result = vol_target(
            signals=signals,
            current_positions={},
            capital_usd=100_000.0,
            target_annual_vol=0.15,
            max_position_pct=0.3,
            max_gross_leverage=2.0,
            rebalance_threshold=0.001,
            instrument_vols=_DEFAULT_VOLS,
        )
        # First pass: both should need rebalance (from 0)
        first_targets = {
            sym: pos["target_notional_usd"]
            for sym, pos in result["target_positions"].items()
        }

        # Second pass: with current = targets → no rebalance
        result2 = vol_target(
            signals=signals,
            current_positions=first_targets,
            capital_usd=100_000.0,
            target_annual_vol=0.15,
            max_position_pct=0.3,
            max_gross_leverage=2.0,
            rebalance_threshold=0.001,
            instrument_vols=_DEFAULT_VOLS,
        )
        assert result2["rebalance_count"] == 0
        for sym in result2["target_positions"]:
            assert result2["target_positions"][sym]["urgency"] == "normal"

    def test_vol_target_neutral_signal_zero_position(self):
        signals = {
            "BTC-USDT": {"direction": "neutral", "strength": 0.0, "confidence": 0.5},
        }
        result = vol_target(
            signals=signals,
            current_positions={},
            capital_usd=100_000.0,
            target_annual_vol=0.15,
            max_position_pct=0.3,
            max_gross_leverage=2.0,
            rebalance_threshold=0.001,
            instrument_vols={"BTC-USDT": 0.8},
        )
        assert result["target_positions"]["BTC-USDT"]["target_notional_usd"] == 0.0


class TestVolTargetOutputKeys:
    def test_vol_target_output_keys(self):
        signals = _make_signals()
        result = vol_target(
            signals=signals,
            current_positions={},
            capital_usd=100_000.0,
            target_annual_vol=0.15,
            max_position_pct=0.3,
            max_gross_leverage=2.0,
            rebalance_threshold=0.001,
            instrument_vols=_DEFAULT_VOLS,
        )
        assert set(result.keys()) == {
            "target_positions", "rebalance_count",
            "total_gross_exposure", "vol_contribution_estimate",
        }

    def test_vol_target_position_entry_keys(self):
        signals = _make_signals()
        result = vol_target(
            signals=signals,
            current_positions={},
            capital_usd=100_000.0,
            target_annual_vol=0.15,
            max_position_pct=0.3,
            max_gross_leverage=2.0,
            rebalance_threshold=0.001,
            instrument_vols=_DEFAULT_VOLS,
        )
        for sym, pos in result["target_positions"].items():
            assert "target_notional_usd" in pos
            assert "urgency" in pos
            assert pos["urgency"] in {"normal", "high"}


class TestVolTargetErrors:
    def test_vol_target_zero_capital_raises(self):
        with pytest.raises(ValueError, match="capital_usd"):
            vol_target(
                signals={"BTC-USDT": {"direction": "long", "strength": 0.5, "confidence": 0.5}},
                current_positions={},
                capital_usd=0.0,
                target_annual_vol=0.15,
                max_position_pct=0.3,
                max_gross_leverage=2.0,
                rebalance_threshold=0.001,
                instrument_vols={"BTC-USDT": 0.8},
            )
