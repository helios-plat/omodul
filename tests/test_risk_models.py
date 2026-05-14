"""Tests for omodul.risk_models.drawdown_circuit_breaker."""
from __future__ import annotations

import numpy as np
import pytest

from omodul.risk_models import drawdown_circuit_breaker


def _healthy_equity(n=20, start=10000.0, drift=0.001):
    """Monotonically growing equity curve."""
    arr = np.zeros(n)
    arr[0] = start
    for i in range(1, n):
        arr[i] = arr[i - 1] * (1 + drift)
    return arr.tolist()


def _params(**overrides):
    defaults = dict(
        daily_loss_halt_pct=0.03,
        weekly_loss_halt_pct=0.08,
        max_position_notional_usd=50_000.0,
        max_total_notional_usd=200_000.0,
        volatility_halt_multiplier=2.0,
        halt_recovery_hours=24,
        recent_realized_vol=0.5,
        baseline_realized_vol=0.5,
    )
    defaults.update(overrides)
    return defaults


class TestDrawdownGreen:
    def test_drawdown_green(self):
        equity = _healthy_equity()
        result = drawdown_circuit_breaker(equity_curve=equity, **_params())
        assert result["status"] == "GREEN"
        assert result["max_position_notional_usd"] == 50_000.0

    def test_drawdown_output_keys(self):
        equity = _healthy_equity()
        result = drawdown_circuit_breaker(equity_curve=equity, **_params())
        required = {
            "status", "daily_loss", "weekly_loss", "max_drawdown",
            "vol_ratio", "max_position_notional_usd",
            "max_total_notional_usd", "halt_recovery_hours",
        }
        assert required.issubset(set(result.keys()))


class TestDrawdownYellow:
    def test_drawdown_vol_yellow(self):
        """High vol with no drawdown → YELLOW."""
        equity = _healthy_equity()
        result = drawdown_circuit_breaker(
            equity_curve=equity,
            **_params(
                recent_realized_vol=2.1,
                baseline_realized_vol=1.0,
                volatility_halt_multiplier=2.0,
            ),
        )
        assert result["status"] == "YELLOW"
        assert result["vol_ratio"] == pytest.approx(2.1)


class TestDrawdownOrange:
    def test_drawdown_daily_orange(self):
        """Equity drops >3% in last bar → ORANGE."""
        equity = _healthy_equity(20)
        equity[-1] = equity[-2] * 0.94  # -6% daily
        result = drawdown_circuit_breaker(
            equity_curve=equity,
            **_params(daily_loss_halt_pct=0.03),
        )
        assert result["status"] == "ORANGE"
        assert result["max_position_notional_usd"] == pytest.approx(25_000.0)  # halved

    def test_drawdown_orange_halves_position(self):
        equity = _healthy_equity()
        equity[-1] = equity[-2] * 0.90  # -10%
        result = drawdown_circuit_breaker(
            equity_curve=equity,
            **_params(daily_loss_halt_pct=0.05, weekly_loss_halt_pct=0.99),
        )
        assert result["status"] == "ORANGE"
        assert result["max_position_notional_usd"] == pytest.approx(25_000.0)


class TestDrawdownRed:
    def test_drawdown_weekly_red(self):
        """Equity drops >8% over 5 bars → RED."""
        equity = [10000.0] * 10
        equity[-1] = 9000.0  # -10% from 5 bars ago
        result = drawdown_circuit_breaker(
            equity_curve=equity,
            **_params(weekly_loss_halt_pct=0.08),
        )
        assert result["status"] == "RED"

    def test_drawdown_red_zeros_positions(self):
        equity = [10000.0] * 10
        equity[-1] = 7000.0  # -30% weekly
        result = drawdown_circuit_breaker(
            equity_curve=equity,
            **_params(weekly_loss_halt_pct=0.08),
        )
        assert result["status"] == "RED"
        assert result["max_position_notional_usd"] == 0.0


class TestDrawdownErrors:
    def test_drawdown_too_short_raises(self):
        with pytest.raises(ValueError, match=">= 2"):
            drawdown_circuit_breaker(
                equity_curve=[10000.0],
                **_params(),
            )

    def test_drawdown_invalid_daily_halt_raises(self):
        with pytest.raises(ValueError, match="daily_loss_halt_pct"):
            drawdown_circuit_breaker(
                equity_curve=_healthy_equity(),
                **_params(daily_loss_halt_pct=0.0),
            )

    def test_drawdown_invalid_weekly_halt_raises(self):
        with pytest.raises(ValueError, match="weekly_loss_halt_pct"):
            drawdown_circuit_breaker(
                equity_curve=_healthy_equity(),
                **_params(weekly_loss_halt_pct=1.0),
            )
