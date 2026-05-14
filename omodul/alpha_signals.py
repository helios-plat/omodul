"""Alpha signal generators for Layer 3 (omodul)."""
from __future__ import annotations

import numpy as np

from oskill.regime import bocpd
from oskill.microstructure import order_flow_imbalance
from oskill.derivatives import basis_decomposition

_VALID_DIRECTION_MODES = {"long_only", "short_only", "long_short"}


def _apply_direction_mode(direction: str, mode: str) -> str:
    """Apply direction_mode filter to a raw direction signal."""
    if mode == "long_short":
        return direction
    if mode == "long_only":
        return direction if direction == "long" else "neutral"
    if mode == "short_only":
        return direction if direction == "short" else "neutral"
    raise ValueError(
        f"direction_mode must be one of {sorted(_VALID_DIRECTION_MODES)}, got {mode!r}"
    )


def bocpd_trend(
    returns: np.ndarray,
    bocpd_hazard: float,
    trend_window: int,
    confidence_threshold: float,
    direction_mode: str = "long_short",
) -> dict:
    """BOCPD-based trend following alpha signal.

    Parameters
    ----------
    returns : np.ndarray
        Array of log returns.
    bocpd_hazard : float
        Hazard rate for BOCPD (probability of regime change per step).
    trend_window : int
        Number of recent bars to use for trend slope computation.
    confidence_threshold : float
        Minimum current_regime_probability to emit a directional signal.
    direction_mode : str
        "long_short", "long_only", or "short_only".

    Returns
    -------
    dict
        AlphaSignal with keys: direction, strength, confidence, metadata.

    Raises
    ------
    ValueError
        If direction_mode is invalid.
    """
    if direction_mode not in _VALID_DIRECTION_MODES:
        raise ValueError(
            f"direction_mode must be one of {sorted(_VALID_DIRECTION_MODES)}, "
            f"got {direction_mode!r}"
        )

    returns_arr = np.asarray(returns, dtype=float)
    result = bocpd(returns_arr, hazard=bocpd_hazard, confidence_threshold=confidence_threshold)

    confidence = float(result["current_regime_probability"])
    current_run_length = int(result["current_run_length"])
    regime_changes = len(result["regime_changes"])

    if confidence < confidence_threshold:
        return {
            "direction": "neutral",
            "strength": 0.0,
            "confidence": confidence,
            "metadata": {
                "current_run_length": current_run_length,
                "regime_changes_detected": regime_changes,
                "trend_slope": 0.0,
            },
        }

    n = len(returns_arr)
    window = min(trend_window, n)
    recent = returns_arr[-window:]
    cumsum_recent = np.cumsum(recent)
    t = np.arange(len(cumsum_recent), dtype=float)
    slope = float(np.polyfit(t, cumsum_recent, 1)[0])

    if slope > 0:
        raw_direction = "long"
    elif slope < 0:
        raw_direction = "short"
    else:
        raw_direction = "neutral"

    direction = _apply_direction_mode(raw_direction, direction_mode)
    strength = min(abs(slope) * 100, 1.0)

    return {
        "direction": direction,
        "strength": strength,
        "confidence": confidence,
        "metadata": {
            "current_run_length": current_run_length,
            "regime_changes_detected": regime_changes,
            "trend_slope": slope,
        },
    }


def ofi_meanrev(
    bid_prices: np.ndarray,
    bid_sizes: np.ndarray,
    ask_prices: np.ndarray,
    ask_sizes: np.ndarray,
    ofi_window_sec: int,
    entry_threshold: float,
    exit_threshold: float,
    direction_mode: str = "long_short",
) -> dict:
    """Order flow imbalance mean-reversion alpha signal.

    Parameters
    ----------
    bid_prices, bid_sizes, ask_prices, ask_sizes : np.ndarray
        Order book data arrays.
    ofi_window_sec : int
        Window in seconds for OFI computation.
    entry_threshold : float
        Z-score threshold to enter a position. Must be > 0.
    exit_threshold : float
        Z-score threshold to exit a position. Must be >= 0.
    direction_mode : str
        "long_short", "long_only", or "short_only".

    Returns
    -------
    dict
        AlphaSignal dict.

    Raises
    ------
    ValueError
        If direction_mode is invalid or entry_threshold <= 0.
    """
    if direction_mode not in _VALID_DIRECTION_MODES:
        raise ValueError(
            f"direction_mode must be one of {sorted(_VALID_DIRECTION_MODES)}, "
            f"got {direction_mode!r}"
        )
    if entry_threshold <= 0:
        raise ValueError(f"entry_threshold must be > 0, got {entry_threshold}")

    ofi_arr = order_flow_imbalance(
        bid_prices, bid_sizes, ask_prices, ask_sizes, window=ofi_window_sec
    )

    window_mean = float(np.nanmean(ofi_arr))
    window_std = float(np.nanstd(ofi_arr))
    ofi_raw = float(ofi_arr[-1]) if not np.isnan(ofi_arr[-1]) else float(np.nanmean(ofi_arr))

    if window_std < 1e-12 or np.isnan(window_std):
        z_score = 0.0
    else:
        z_score = (ofi_raw - window_mean) / window_std

    if abs(z_score) < entry_threshold:
        raw_direction = "neutral"
    elif z_score > entry_threshold:
        # Strong buy pressure → mean reversion → go SHORT
        raw_direction = "short"
    else:
        # Strong sell pressure → mean reversion → go LONG
        raw_direction = "long"

    direction = _apply_direction_mode(raw_direction, direction_mode)
    strength = min(abs(z_score) / (entry_threshold * 2), 1.0)

    return {
        "direction": direction,
        "strength": strength,
        "confidence": min(abs(z_score) / max(entry_threshold, 1e-9), 1.0),
        "metadata": {
            "ofi_z_score": z_score,
            "ofi_raw": ofi_raw,
            "window_mean": window_mean,
            "window_std": window_std,
        },
    }


def funding_rate_directional(
    spot_prices: np.ndarray,
    perp_prices: np.ndarray,
    funding_rates: np.ndarray,
    funding_threshold_bps_long: float,
    funding_threshold_bps_short: float,
    basis_filter_bps: float,
    lookback_hours: int,
    direction_mode: str = "long_short",
) -> dict:
    """Funding-rate-based directional alpha signal.

    Parameters
    ----------
    spot_prices, perp_prices, funding_rates : np.ndarray
        Price and funding rate arrays (must have equal length).
    funding_threshold_bps_long : float
        If avg funding < -this (bps), go long.
    funding_threshold_bps_short : float
        If avg funding > +this (bps), go short.
    basis_filter_bps : float
        Max residual in bps before marking signal neutral (anomaly avoidance).
    lookback_hours : int
        Hours to look back (must be >= 8, since funding is 8h intervals).
    direction_mode : str
        "long_short", "long_only", or "short_only".

    Returns
    -------
    dict
        AlphaSignal dict.

    Raises
    ------
    ValueError
        If arrays have different lengths, lookback_hours < 8, or invalid mode.
    """
    if direction_mode not in _VALID_DIRECTION_MODES:
        raise ValueError(
            f"direction_mode must be one of {sorted(_VALID_DIRECTION_MODES)}, "
            f"got {direction_mode!r}"
        )
    if lookback_hours < 8:
        raise ValueError(f"lookback_hours must be >= 8, got {lookback_hours}")

    spot_arr = np.asarray(spot_prices, dtype=float)
    perp_arr = np.asarray(perp_prices, dtype=float)
    fund_arr = np.asarray(funding_rates, dtype=float)

    if not (len(spot_arr) == len(perp_arr) == len(fund_arr)):
        raise ValueError(
            f"spot_prices, perp_prices, funding_rates must have equal length, "
            f"got {len(spot_arr)}, {len(perp_arr)}, {len(fund_arr)}"
        )

    result = basis_decomposition(spot_arr, perp_arr, fund_arr, funding_interval_hours=8.0)

    N = max(1, lookback_hours // 8)

    annualized_basis_bps = float(result["annualized_basis_pct"][-1]) * 10000
    residual_bps = float(abs(result["residual"][-1]) / spot_arr[-1]) * 10000

    if residual_bps > basis_filter_bps:
        return {
            "direction": "neutral",
            "strength": 0.0,
            "confidence": 0.0,
            "metadata": {
                "avg_funding_bps": float(np.mean(fund_arr[-N:]) * 10000),
                "annualized_basis_bps": annualized_basis_bps,
                "residual_bps": residual_bps,
            },
        }

    avg_funding_bps = float(np.mean(fund_arr[-N:]) * 10000)

    if avg_funding_bps < -funding_threshold_bps_long:
        raw_direction = "long"
    elif avg_funding_bps > funding_threshold_bps_short:
        raw_direction = "short"
    else:
        raw_direction = "neutral"

    direction = _apply_direction_mode(raw_direction, direction_mode)
    denom = max(funding_threshold_bps_long, funding_threshold_bps_short)
    strength = min(abs(avg_funding_bps) / denom, 1.0) if denom > 0 else 0.0

    return {
        "direction": direction,
        "strength": strength,
        "confidence": strength,
        "metadata": {
            "avg_funding_bps": avg_funding_bps,
            "annualized_basis_bps": annualized_basis_bps,
            "residual_bps": residual_bps,
        },
    }
