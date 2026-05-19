"""Risk models including drawdown circuit breaker."""
from __future__ import annotations

import numpy as np
import pandas as pd

from oprim.finance import drawdown_curve


def drawdown_circuit_breaker(
    equity_curve: list | np.ndarray,
    daily_loss_halt_pct: float,
    weekly_loss_halt_pct: float,
    max_position_notional_usd: float,
    max_total_notional_usd: float,
    volatility_halt_multiplier: float,
    halt_recovery_hours: int,
    recent_realized_vol: float,
    baseline_realized_vol: float,
) -> dict:
    """Drawdown-based circuit breaker for position sizing control.

    Parameters
    ----------
    equity_curve : array-like
        Equity values over time. Must have >= 2 elements.
    daily_loss_halt_pct : float
        Daily loss fraction to trigger ORANGE halt (0 < value < 1).
    weekly_loss_halt_pct : float
        Weekly loss fraction to trigger RED halt (0 < value < 1).
    max_position_notional_usd : float
        Base maximum per-position notional.
    max_total_notional_usd : float
        Base maximum total notional.
    volatility_halt_multiplier : float
        Ratio of recent/baseline vol above which YELLOW is triggered.
    halt_recovery_hours : int
        Hours until halt can be reviewed.
    recent_realized_vol : float
        Recent realized volatility.
    baseline_realized_vol : float
        Baseline / historical average volatility.

    Returns
    -------
    dict
        Status dict with keys: status, daily_loss, weekly_loss, max_drawdown,
        vol_ratio, max_position_notional_usd, max_total_notional_usd,
        halt_recovery_hours.

    Raises
    ------
    ValueError
        If equity_curve has < 2 elements or loss thresholds are invalid.
    """
    arr = np.asarray(equity_curve, dtype=float)

    if len(arr) < 2:
        raise ValueError(
            f"equity_curve must have >= 2 elements, got {len(arr)}"
        )
    if not (0 < daily_loss_halt_pct < 1):
        raise ValueError(
            f"daily_loss_halt_pct must be in (0, 1), got {daily_loss_halt_pct}"
        )
    if not (0 < weekly_loss_halt_pct < 1):
        raise ValueError(
            f"weekly_loss_halt_pct must be in (0, 1), got {weekly_loss_halt_pct}"
        )

    n = len(arr)
    daily_loss = float((arr[-1] - arr[-2]) / arr[-2])
    weekly_start_idx = max(0, n - 5)
    weekly_loss = float((arr[-1] - arr[weekly_start_idx]) / arr[weekly_start_idx])

    dd_result = drawdown_curve(pd.Series(arr), input_type="equity")
    max_drawdown = float(dd_result["max_drawdown"])

    vol_ratio = float(recent_realized_vol / baseline_realized_vol) if baseline_realized_vol > 0 else 1.0

    # Determine status (worst condition wins)
    status = "GREEN"

    if vol_ratio > volatility_halt_multiplier:
        status = "YELLOW"

    if abs(daily_loss) > daily_loss_halt_pct:
        status = "ORANGE"

    if abs(weekly_loss) > weekly_loss_halt_pct:
        status = "RED"

    # Apply position limits based on status
    effective_max_position = max_position_notional_usd
    if status == "ORANGE":
        effective_max_position = max_position_notional_usd / 2.0
    elif status == "RED":
        effective_max_position = 0.0

    return {
        "status": status,
        "daily_loss": daily_loss,
        "weekly_loss": weekly_loss,
        "max_drawdown": max_drawdown,
        "vol_ratio": vol_ratio,
        "max_position_notional_usd": effective_max_position,
        "max_total_notional_usd": max_total_notional_usd,
        "halt_recovery_hours": halt_recovery_hours,
    }
