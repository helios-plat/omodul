"""Risk gate branch tests for omodul.strategies.bocpd_trend_following (Bug backfill §Step 4).

Tests cover all four risk_gate_status outcomes:
  GREEN  — normal conditions, strategy proceeds and produces signals
  YELLOW — elevated volatility, strategy still proceeds
  ORANGE — daily loss breach, early-return with neutral signals
  RED    — weekly loss breach, early-return with zero positions
"""
from __future__ import annotations

import numpy as np
import pytest

from omodul.strategies import bocpd_trend_following

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SYMBOLS = ["BTC", "ETH"]


def _base_config(**overrides) -> dict:
    """Return a minimal valid config with optional field overrides."""
    cfg = {
        "bocpd_hazard": 0.01,
        "trend_window": 10,
        "confidence_threshold": 0.5,
        "direction_mode": "long_short",
        "target_annual_vol": 0.15,
        "max_position_pct": 0.20,
        "max_gross_leverage": 2.0,
        "rebalance_threshold": 0.01,
        "daily_loss_halt_pct": -0.02,       # halt if daily loss < -2 %
        "weekly_loss_halt_pct": -0.05,      # halt if weekly loss < -5 %
        "volatility_halt_multiplier": 3.0,  # halt (YELLOW) if vol_ratio > 3
        "baseline_realized_vol": 0.01,
        "daily_volume_usd": 1e9,
        "realized_vol_30d": 0.01,
    }
    cfg.update(overrides)
    return cfg


def _base_market_state(equity_curve: list[float], **overrides) -> dict:
    """Return a minimal valid market_state with optional field overrides."""
    n = len(equity_curve)
    features = {}
    for sym in _SYMBOLS:
        features[f"returns_{sym}"] = list(np.random.default_rng(42).normal(0.001, 0.01, 50))

    ms = {
        "symbols": _SYMBOLS,
        "features": features,
        "current_positions": {sym: 0.0 for sym in _SYMBOLS},
        "capital_usd": 100_000.0,
        "equity_curve": equity_curve,
    }
    ms.update(overrides)
    return ms


# ---------------------------------------------------------------------------
# 1. GREEN status — normal conditions
# ---------------------------------------------------------------------------


def test_green_status() -> None:
    """Normal equity curve with no loss breaches → GREEN gate, strategy produces signals."""
    # Gently rising equity curve — no daily or weekly loss, low vol ratio
    equity = [100_000.0 + i * 100 for i in range(10)]  # monotonically increasing
    config = _base_config(
        realized_vol_30d=0.005,        # well below baseline * multiplier = 0.03
        baseline_realized_vol=0.01,
        volatility_halt_multiplier=3.0,
    )
    market_state = _base_market_state(equity)

    result = bocpd_trend_following(market_state, config)

    assert result["risk_gate_status"] == "GREEN", (
        f"Expected GREEN, got {result['risk_gate_status']}"
    )
    # Strategy should NOT have early-returned — signals dict must be populated
    assert "signals" in result
    assert set(result["signals"].keys()) == set(_SYMBOLS), (
        "GREEN path must populate signals for all symbols"
    )
    # target_positions must be present (even if zero due to no trend signal)
    assert "target_positions" in result
    assert set(result["target_positions"].keys()) == set(_SYMBOLS)


# ---------------------------------------------------------------------------
# 2. YELLOW status — elevated volatility
# ---------------------------------------------------------------------------


def test_yellow_status() -> None:
    """Realized vol >> baseline * multiplier → YELLOW gate; strategy still proceeds."""
    # Flat equity curve — no loss breach triggers
    equity = [100_000.0] * 10

    config = _base_config(
        realized_vol_30d=0.05,          # vol_ratio = 0.05 / 0.01 = 5 > multiplier 3
        baseline_realized_vol=0.01,
        volatility_halt_multiplier=3.0,
        daily_loss_halt_pct=-0.10,      # very loose — won't trigger
        weekly_loss_halt_pct=-0.50,     # very loose — won't trigger
    )
    market_state = _base_market_state(equity)

    result = bocpd_trend_following(market_state, config)

    assert result["risk_gate_status"] == "YELLOW", (
        f"Expected YELLOW, got {result['risk_gate_status']}"
    )
    # YELLOW does NOT early-return — signals must be present for all symbols
    assert set(result["signals"].keys()) == set(_SYMBOLS), (
        "YELLOW path must not early-return; signals must be present"
    )


# ---------------------------------------------------------------------------
# 3. ORANGE status — daily loss breach
# ---------------------------------------------------------------------------


def test_orange_status() -> None:
    """Daily loss below halt threshold → ORANGE gate, early-return with neutral signals."""
    # Equity drops sharply on the last day: -5% daily loss (halt at -2%)
    base = 100_000.0
    # 9 prior days flat, then big drop
    equity = [base] * 9 + [base * 0.94]   # daily loss ≈ -6 %

    config = _base_config(
        daily_loss_halt_pct=-0.02,          # trigger if daily_loss < -2 %
        weekly_loss_halt_pct=-0.50,         # loose — won't trigger RED
        realized_vol_30d=0.005,             # low vol — won't trigger YELLOW
        baseline_realized_vol=0.01,
        volatility_halt_multiplier=3.0,
    )
    market_state = _base_market_state(equity)

    result = bocpd_trend_following(market_state, config)

    assert result["risk_gate_status"] == "ORANGE", (
        f"Expected ORANGE, got {result['risk_gate_status']}"
    )
    # ORANGE early-returns with neutral / zero signals
    for sym in _SYMBOLS:
        sig = result["signals"][sym]
        assert sig["direction"] == "neutral", (
            f"{sym}: expected direction='neutral' on ORANGE, got {sig['direction']}"
        )
        assert sig["strength"] == 0.0, (
            f"{sym}: expected strength=0.0 on ORANGE, got {sig['strength']}"
        )
        pos = result["target_positions"][sym]
        assert pos["target_notional_usd"] == 0.0, (
            f"{sym}: expected target_notional_usd=0.0 on ORANGE"
        )


# ---------------------------------------------------------------------------
# 4. RED status — weekly loss breach
# ---------------------------------------------------------------------------


def test_red_status() -> None:
    """Weekly loss below halt threshold → RED gate, early-return with zero positions."""
    # Build a curve where the last value is >> 5 % below 5 days ago
    base = 100_000.0
    # 5 days ago was 100k, today is 94k → weekly loss ≈ -6% (halt at -5%)
    equity = [base * 0.94] * 5 + [base] + [base * 0.94]  # 7 points; last vs index -5

    # Ensure weekly_loss < weekly_loss_halt_pct = -0.05
    # weekly_start = max(0, 7-5) = 2; equity[2] = 94k, equity[-1] = 94k → 0% loss
    # Need a different shape:
    equity2 = [base] * 5 + [base * 0.94]   # 6 elements; weekly_start=1; equity[1]=100k, last=94k
    # weekly_loss = (94k - 100k) / 100k = -0.06 < -0.05  → RED

    config = _base_config(
        weekly_loss_halt_pct=-0.05,         # trigger if weekly_loss < -5 %
        daily_loss_halt_pct=-0.50,          # loose — won't trigger ORANGE alone
        realized_vol_30d=0.005,             # low vol — no YELLOW
        baseline_realized_vol=0.01,
        volatility_halt_multiplier=3.0,
    )
    market_state = _base_market_state(equity2)

    result = bocpd_trend_following(market_state, config)

    assert result["risk_gate_status"] == "RED", (
        f"Expected RED, got {result['risk_gate_status']}"
    )
    # RED early-returns with neutral signals and zero target positions
    for sym in _SYMBOLS:
        sig = result["signals"][sym]
        assert sig["direction"] == "neutral", (
            f"{sym}: expected direction='neutral' on RED, got {sig['direction']}"
        )
        pos = result["target_positions"][sym]
        assert pos["target_notional_usd"] == 0.0, (
            f"{sym}: expected target_notional_usd=0.0 on RED"
        )
