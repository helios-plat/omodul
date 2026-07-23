"""Portfolio management modules built on oprim/oskill primitives."""

from __future__ import annotations

import numpy as np

from omodul.portfolio.cvar_risk_workflow import CvarRiskConfig, cvar_risk_workflow

__all__ = ["CvarRiskConfig", "cvar_risk_workflow"]


def kelly_allocator(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    half_kelly: bool = True,
    max_fraction: float = 0.25,
    cost_per_trade: float = 0.0,
) -> dict[str, float]:
    """Cost-adjusted Kelly criterion position sizing.

    f* = (W × R - (1-W)) / R, where W = win_rate, R = avg_win/avg_loss.
    Optionally applies half-Kelly and caps.

    Parameters
    ----------
    win_rate : float
        Historical win probability (0-1).
    avg_win : float
        Average winning trade return (positive).
    avg_loss : float
        Average losing trade return (positive, absolute value).
    half_kelly : bool
        If True, use f*/2 (more conservative).
    max_fraction : float
        Maximum allocation fraction cap.
    cost_per_trade : float
        Round-trip cost to subtract from expected edge.

    Returns
    -------
    dict
        "kelly_fraction": raw f*, "position_fraction": final (after half/cap),
        "edge": expected edge per trade.

    References
    ----------
    .. [1] Kelly, J.L. (1956). A New Interpretation of Information Rate.
    .. [2] Extraction source: Selene project, services/portfolio/capital/kelly.py:kelly_fraction
    """
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        return {"kelly_fraction": 0.0, "position_fraction": 0.0, "edge": 0.0}

    R = avg_win / avg_loss
    edge = win_rate * R - (1 - win_rate) - cost_per_trade * R
    f_star = edge / R if R > 0 else 0.0

    if f_star <= 0:
        return {"kelly_fraction": f_star, "position_fraction": 0.0, "edge": edge}

    position = f_star / 2 if half_kelly else f_star
    position = min(position, max_fraction)

    return {
        "kelly_fraction": round(f_star, 6),
        "position_fraction": round(position, 6),
        "edge": round(edge, 6),
    }


def risk_parity(
    volatilities: dict[str, float],
    target_total: float = 1.0,
) -> dict[str, float]:
    """Inverse-volatility risk parity allocation.

    Each strategy gets weight proportional to 1/vol, so each contributes
    equal risk to the portfolio.

    Parameters
    ----------
    volatilities : dict[str, float]
        Strategy name → annualized volatility.
    target_total : float
        Target total allocation (default 1.0 = 100%).

    Returns
    -------
    dict[str, float]
        Strategy name → allocation weight (sums to target_total).

    References
    ----------
    .. [1] Qian, E. (2005). Risk Parity Portfolios.
    .. [2] Extraction source: Selene project, services/portfolio/capital/kelly.py:risk_parity_weights
    """
    if not volatilities:
        return {}
    inv_vols = {k: 1.0 / v for k, v in volatilities.items() if v > 0}
    if not inv_vols:
        n = len(volatilities)
        return {k: target_total / n for k in volatilities}
    total_inv = sum(inv_vols.values())
    return {k: round(v / total_inv * target_total, 6) for k, v in inv_vols.items()}


def execution_cost_model(
    notional_usd: float,
    spread_bps: float = 1.0,
    daily_volume_usd: float = 1e9,
    urgency: float = 0.5,
) -> dict[str, float]:
    """Three-component execution cost model.

    Components:
    1. Half-spread cost
    2. Square-root market impact (Kyle 1985)
    3. Timing cost (urgency-dependent)

    Parameters
    ----------
    notional_usd : float
        Order notional in USD.
    spread_bps : float
        Bid-ask spread in basis points.
    daily_volume_usd : float
        Average daily volume in USD.
    urgency : float
        Urgency parameter (0 = patient, 1 = aggressive).

    Returns
    -------
    dict
        "spread_cost_bps", "impact_cost_bps", "timing_cost_bps",
        "total_cost_bps", "total_cost_usd".

    References
    ----------
    .. [1] Kyle, A.S. (1985). Continuous Auctions and Insider Trading.
    .. [2] Almgren, R. & Chriss, N. (2001). Optimal execution of portfolio transactions.
    .. [3] Extraction source: Selene project, services/execution/slippage/model.py:SlippageModel.estimate
    """
    # 1. Half-spread
    spread_cost = spread_bps / 2.0

    # 2. Square-root impact: σ × √(Q/V)
    participation = notional_usd / daily_volume_usd if daily_volume_usd > 0 else 0
    impact_cost = 10.0 * np.sqrt(participation)  # 10 bps at 1% participation

    # 3. Timing cost (opportunity cost of waiting)
    timing_cost = urgency * spread_bps * 0.5

    total_bps = spread_cost + impact_cost + timing_cost
    total_usd = notional_usd * total_bps / 10000

    return {
        "spread_cost_bps": round(spread_cost, 4),
        "impact_cost_bps": round(impact_cost, 4),
        "timing_cost_bps": round(timing_cost, 4),
        "total_cost_bps": round(total_bps, 4),
        "total_cost_usd": round(total_usd, 2),
    }
