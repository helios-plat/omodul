"""Execution models for order scheduling and cost estimation."""
from __future__ import annotations

import logging
import numpy as np

log = logging.getLogger(__name__)

try:
    from oskill.cost import crypto_market_impact_sigmoid
except ImportError:
    crypto_market_impact_sigmoid = None

_SUPPORTED_COST_MODEL = "crypto_market_impact_sigmoid"

_impact_fn_name = (
    "crypto_market_impact_sigmoid" if crypto_market_impact_sigmoid is not None
    else "_crypto_impact_fallback"
)
log.info("impact_model_active: %s", _impact_fn_name)


def _crypto_impact_fallback(
    notional_usd: float,
    daily_volume_usd: float = 1e9,
    realized_vol_30d: float = 0.02,
    **kwargs,
) -> dict:
    participation = notional_usd / daily_volume_usd if daily_volume_usd > 0 else 0
    impact_bps = 10.0 * np.sqrt(participation) * (1 + realized_vol_30d)
    return {"impact_bps": impact_bps, "impact_usd": notional_usd * impact_bps / 10000}


def twap_with_impact(
    target_notional_usd: float,
    daily_volume_usd: float,
    realized_vol_30d: float,
    slice_duration_sec: int,
    n_slices: int,
    cost_model_name: str,
    cost_model_params: dict,
    urgency: str = "normal",
) -> dict:
    """TWAP schedule with market impact estimation.

    Parameters
    ----------
    target_notional_usd : float
        Total order size in USD.
    daily_volume_usd : float
        Average daily volume of the instrument in USD.
    realized_vol_30d : float
        30-day realized volatility of the instrument.
    slice_duration_sec : int
        Duration of each TWAP slice in seconds.
    n_slices : int
        Number of TWAP slices (>= 1).
    cost_model_name : str
        Must be "crypto_market_impact_sigmoid".
    cost_model_params : dict
        Additional kwargs for the cost model (sigmoid_center, sigmoid_scale).
    urgency : str
        "normal" or "high". Stored as metadata, does not change slice count.

    Returns
    -------
    dict
        TWAP plan with keys: schedule, total_expected_impact_bps,
        total_slippage_estimate_usd, urgency.

    Raises
    ------
    ValueError
        If cost_model_name is wrong, n_slices < 1, or target_notional <= 0.
    """
    if cost_model_name != _SUPPORTED_COST_MODEL:
        raise ValueError(
            f"cost_model_name must be {_SUPPORTED_COST_MODEL!r}, "
            f"got {cost_model_name!r}"
        )
    if n_slices < 1:
        raise ValueError(f"n_slices must be >= 1, got {n_slices}")
    if target_notional_usd <= 0:
        raise ValueError(f"target_notional_usd must be > 0, got {target_notional_usd}")

    slice_notional = target_notional_usd / n_slices
    schedule = []
    total_slippage_usd = 0.0
    total_impact_bps = 0.0

    _impact_fn = crypto_market_impact_sigmoid or _crypto_impact_fallback
    for i in range(n_slices):
        impact_result = _impact_fn(
            slice_notional,
            daily_volume_usd,
            realized_vol_30d,
            **cost_model_params,
        )
        impact_bps = float(impact_result["impact_bps"])
        slippage_usd = slice_notional * impact_bps / 10000.0
        total_impact_bps += impact_bps
        total_slippage_usd += slippage_usd
        schedule.append({
            "slice_index": i,
            "offset_sec": i * slice_duration_sec,
            "notional_usd": slice_notional,
            "expected_impact_bps": impact_bps,
        })

    return {
        "schedule": schedule,
        "total_expected_impact_bps": total_impact_bps,
        "total_slippage_estimate_usd": total_slippage_usd,
        "urgency": urgency,
    }


def aggressive_limit(
    target_notional_usd: float,
    cost_model_name: str,
    cost_model_params: dict,
    limit_offset_bps: int,
    timeout_sec: int,
    on_timeout: str,
    max_slippage_bps: int,
) -> dict:
    """Aggressive limit order with cost guard.

    Parameters
    ----------
    target_notional_usd : float
        Order size in USD.
    cost_model_name : str
        Must be "crypto_market_impact_sigmoid".
    cost_model_params : dict
        kwargs for the cost model (daily_volume_usd, realized_vol_30d, etc.).
    limit_offset_bps : int
        Limit price offset in basis points from mid.
    timeout_sec : int
        Seconds before the order times out.
    on_timeout : str
        "market" or "cancel".
    max_slippage_bps : int
        Maximum acceptable slippage in bps. Must be > 0.

    Returns
    -------
    dict
        Order spec with keys: limit_offset_bps, timeout_sec, on_timeout,
        max_slippage_bps, estimated_impact_bps, execute.

    Raises
    ------
    ValueError
        If cost_model_name wrong, on_timeout invalid, or max_slippage_bps <= 0.
    """
    if cost_model_name != _SUPPORTED_COST_MODEL:
        raise ValueError(
            f"cost_model_name must be {_SUPPORTED_COST_MODEL!r}, "
            f"got {cost_model_name!r}"
        )
    if on_timeout not in {"market", "cancel"}:
        raise ValueError(
            f"on_timeout must be 'market' or 'cancel', got {on_timeout!r}"
        )
    if max_slippage_bps <= 0:
        raise ValueError(f"max_slippage_bps must be > 0, got {max_slippage_bps}")

    _impact_fn2 = crypto_market_impact_sigmoid or _crypto_impact_fallback
    impact_result = _impact_fn2(
        target_notional_usd,
        **cost_model_params,
    )
    estimated_impact_bps = float(impact_result["impact_bps"])
    execute = estimated_impact_bps <= max_slippage_bps

    return {
        "limit_offset_bps": limit_offset_bps,
        "timeout_sec": timeout_sec,
        "on_timeout": on_timeout,
        "max_slippage_bps": max_slippage_bps,
        "estimated_impact_bps": estimated_impact_bps,
        "execute": execute,
    }
