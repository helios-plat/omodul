"""Portfolio construction using volatility targeting."""
from __future__ import annotations

import numpy as np

try:
    from oskill.portfolio import position_sizing_vol_target
except ImportError:
    position_sizing_vol_target = None


def _position_sizing_vol_target(
    signal_strength: float,
    instrument_vol_annual: float,
    portfolio_target_vol: float,
    current_capital: float,
    max_position_pct: float,
) -> dict:
    """Local fallback: vol-scaled position sizing."""
    if instrument_vol_annual <= 0:
        return {"target_notional_usd": 0.0, "fraction_of_capital": 0.0}
    fraction = signal_strength * (portfolio_target_vol / instrument_vol_annual)
    fraction = min(fraction, max_position_pct)
    return {
        "target_notional_usd": fraction * current_capital,
        "fraction_of_capital": fraction,
    }


def vol_target(
    signals: dict,
    current_positions: dict,
    capital_usd: float,
    target_annual_vol: float,
    max_position_pct: float,
    max_gross_leverage: float,
    rebalance_threshold: float,
    instrument_vols: dict,
) -> dict:
    """Volatility-targeted portfolio construction.

    Parameters
    ----------
    signals : dict[str, dict]
        AlphaSignal dict per symbol (keys: direction, strength, confidence, metadata).
    current_positions : dict[str, float]
        Current notional USD exposure per symbol.
    capital_usd : float
        Total portfolio capital in USD.
    target_annual_vol : float
        Target annual volatility for the portfolio (e.g. 0.15 for 15%).
    max_position_pct : float
        Maximum position as fraction of capital (0-1).
    max_gross_leverage : float
        Maximum total gross exposure as multiple of capital.
    rebalance_threshold : float
        Minimum abs(delta / capital_usd) to trigger a rebalance.
    instrument_vols : dict[str, float]
        Annual realized volatility per symbol.

    Returns
    -------
    dict
        Portfolio target with keys: target_positions, rebalance_count,
        total_gross_exposure, vol_contribution_estimate.

    Raises
    ------
    ValueError
        If capital_usd <= 0.
    """
    if capital_usd <= 0:
        raise ValueError(f"capital_usd must be > 0, got {capital_usd}")

    target_notionals: dict[str, float] = {}

    for symbol, signal in signals.items():
        direction = signal["direction"]
        strength = float(signal["strength"])

        if direction == "long":
            effective_strength = strength
        elif direction == "short":
            effective_strength = -strength
        else:
            effective_strength = 0.0

        if abs(effective_strength) < 1e-9:
            target_notionals[symbol] = 0.0
        else:
            inst_vol = float(instrument_vols.get(symbol, 0.5))
            _sizer = position_sizing_vol_target or _position_sizing_vol_target
            sizing = _sizer(
                signal_strength=abs(effective_strength),
                instrument_vol_annual=inst_vol,
                portfolio_target_vol=target_annual_vol,
                current_capital=capital_usd,
                max_position_pct=max_position_pct,
            )
            notional = float(sizing["target_notional_usd"])
            target_notionals[symbol] = notional * np.sign(effective_strength)

    # Scale down if total gross exceeds leverage cap
    total_gross = sum(abs(v) for v in target_notionals.values())
    max_gross = capital_usd * max_gross_leverage
    if total_gross > max_gross and total_gross > 0:
        scale = max_gross / total_gross
        target_notionals = {sym: v * scale for sym, v in target_notionals.items()}
        total_gross = max_gross

    # Compute rebalance decisions
    rebalance_count = 0
    target_positions: dict[str, dict] = {}
    vol_contribution_estimate = 0.0

    for symbol, target_notional in target_notionals.items():
        current = float(current_positions.get(symbol, 0.0))
        delta = target_notional - current
        needs_rebalance = abs(delta) / capital_usd >= rebalance_threshold

        if needs_rebalance:
            urgency = "high"
            rebalance_count += 1
            inst_vol = float(instrument_vols.get(symbol, 0.5))
            vol_contribution_estimate += abs(target_notional / capital_usd) * inst_vol
        else:
            urgency = "normal"

        target_positions[symbol] = {
            "target_notional_usd": target_notional,
            "urgency": urgency,
        }

    return {
        "target_positions": target_positions,
        "rebalance_count": rebalance_count,
        "total_gross_exposure": total_gross,
        "vol_contribution_estimate": vol_contribution_estimate,
    }
