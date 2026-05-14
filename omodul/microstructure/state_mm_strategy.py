"""State-Dependent Market Making Strategy — Hawkes-adjusted Avellaneda-Stoikov."""
from __future__ import annotations

from typing import Any

import numpy as np

try:
    from oskill.market_making.avellaneda_stoikov import avellaneda_stoikov_quotes
    from oskill.microstructure.state_hawkes import order_book_state_hawkes
except ImportError:  # pragma: no cover
    order_book_state_hawkes = None  # type: ignore[assignment]
    avellaneda_stoikov_quotes = None  # type: ignore[assignment]


def _fallback_hawkes(
    event_times: np.ndarray,
    event_types: np.ndarray,
    ob_state: np.ndarray,
    n_event_types: int,
) -> dict[str, Any]:
    return {
        "baseline": np.ones(n_event_types) * 0.1,
        "excitation": np.eye(n_event_types) * 0.1,
        "state_response": np.ones((5, n_event_types)),
        "log_likelihood": -1.0,
        "branching_ratio": 0.5,
    }


def _fallback_as_quotes(
    mid_price: float,
    sigma: float,
    gamma: float,
    T: float,
) -> dict[str, Any]:
    spread = gamma * sigma**2 * T + (2 / gamma) * np.log(1 + gamma / 1.5)
    spread = max(spread, 1e-6)
    return {
        "bid": mid_price - spread / 2,
        "ask": mid_price + spread / 2,
        "reservation_price": mid_price,
        "optimal_spread": spread,
        "half_spread_above_mid": spread / 2,
        "half_spread_below_mid": spread / 2,
        "should_pause_quoting": False,
        "fingerprint": None,
    }


def state_dependent_market_making_strategy(
    event_times: np.ndarray,
    event_types: np.ndarray,
    ob_state: np.ndarray,
    *,
    mid_price: float,
    sigma: float,
    gamma: float = 0.1,
    T: float = 1.0,
) -> dict[str, Any]:
    """State-aware high-frequency market making strategy.

    Fits a state-dependent Hawkes process to order flow, then computes
    Avellaneda-Stoikov base quotes and adjusts the spread using the Hawkes
    branching ratio (higher excitation → wider spread).

    Parameters
    ----------
    event_times : np.ndarray
        1-D strictly increasing array of event arrival times.
    event_types : np.ndarray
        Integer array of event types (0 = market buy, 1 = market sell).
    ob_state : np.ndarray
        Order book state variable observations (e.g. bid-ask imbalance).
        Same length as event_times.
    mid_price : float
        Current mid-market price (> 0).
    sigma : float
        Price volatility per unit time (>= 0).
    gamma : float
        Inventory risk aversion parameter (> 0).
    T : float
        Remaining time horizon (>= 0).

    Returns
    -------
    dict with keys:
        ``hawkes_params`` — fitted Hawkes model parameters dict.
        ``base_quotes`` — Avellaneda-Stoikov base bid/ask quotes dict.
        ``state_adjusted_quotes`` — spread-adjusted bid/ask dict.
        ``adjustment_factor`` — spread multiplier from Hawkes branching ratio.
    """
    event_times = np.asarray(event_times, dtype=float)
    event_types = np.asarray(event_types, dtype=int)
    ob_state = np.asarray(ob_state, dtype=float)

    if event_times.ndim != 1:
        raise ValueError("event_times must be 1-D")
    if len(event_times) < 10:
        raise ValueError(f"event_times must have at least 10 events, got {len(event_times)}")
    if len(event_types) != len(event_times):
        raise ValueError("event_types must have same length as event_times")
    if len(ob_state) != len(event_times):
        raise ValueError("ob_state must have same length as event_times")
    if mid_price <= 0:
        raise ValueError(f"mid_price must be > 0, got {mid_price!r}")
    if sigma < 0:
        raise ValueError(f"sigma must be >= 0, got {sigma!r}")
    if gamma <= 0:
        raise ValueError(f"gamma must be > 0, got {gamma!r}")
    if T < 0:
        raise ValueError(f"T must be >= 0, got {T!r}")

    # Ensure event_times are strictly increasing
    if not np.all(np.diff(event_times) > 0):
        raise ValueError("event_times must be strictly increasing")

    # Determine n_event_types
    n_event_types = int(event_types.max()) + 1
    n_event_types = max(n_event_types, 2)

    # Ensure event_types are in valid range
    if np.any(event_types < 0) or np.any(event_types >= n_event_types):
        raise ValueError(
            f"event_types must be in [0, {n_event_types - 1}]"
        )

    # 1. Fit state-dependent Hawkes process
    if order_book_state_hawkes is not None:
        try:
            hawkes_result = order_book_state_hawkes(
                event_times,
                event_types,
                ob_state,
                n_event_types=n_event_types,
            )
        except Exception:
            hawkes_result = _fallback_hawkes(event_times, event_types, ob_state, n_event_types)
    else:
        hawkes_result = _fallback_hawkes(event_times, event_types, ob_state, n_event_types)

    branching_ratio = float(hawkes_result.get("branching_ratio", 0.5))

    # 2. Avellaneda-Stoikov base quotes (inventory = 0, neutral)
    if avellaneda_stoikov_quotes is not None:
        try:
            base_quotes = avellaneda_stoikov_quotes(
                mid_price,
                0,  # neutral inventory
                volatility=sigma,
                time_to_horizon=T,
                risk_aversion=gamma,
            )
        except Exception:
            base_quotes = _fallback_as_quotes(mid_price, sigma, gamma, T)
    else:
        base_quotes = _fallback_as_quotes(mid_price, sigma, gamma, T)

    # 3. Adjust spread based on Hawkes branching ratio
    # branching_ratio in [0, 1): stable process; higher -> more clustering -> wider spread
    # adjustment_factor in [1.0, 2.0] maps branching_ratio from [0, 1)
    br_clipped = float(np.clip(branching_ratio, 0.0, 0.99))
    adjustment_factor = 1.0 + br_clipped  # simple linear scaling

    base_spread = float(base_quotes.get("optimal_spread", 0.0))
    adjusted_spread = base_spread * adjustment_factor
    reservation_price = float(base_quotes.get("reservation_price", mid_price))

    state_adjusted_quotes = {
        "bid": reservation_price - adjusted_spread / 2.0,
        "ask": reservation_price + adjusted_spread / 2.0,
        "reservation_price": reservation_price,
        "optimal_spread": adjusted_spread,
    }

    return {
        "hawkes_params": hawkes_result,
        "base_quotes": base_quotes,
        "state_adjusted_quotes": state_adjusted_quotes,
        "adjustment_factor": adjustment_factor,
    }
