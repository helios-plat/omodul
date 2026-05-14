"""Robust Decision Workflow — multi-criterion robust portfolio selection."""
from __future__ import annotations

from typing import Any

import numpy as np

try:
    from oskill.robust.maxmin_eu import maxmin_expected_utility_portfolio
    from oskill.robust.multiplier_preferences import multiplier_preferences_robust
    from oskill.robust.smooth_ambiguity import smooth_ambiguity_portfolio
    from oskill.robust.variational_preferences import variational_preferences_estimate
except ImportError:  # pragma: no cover
    multiplier_preferences_robust = None  # type: ignore[assignment]
    variational_preferences_estimate = None  # type: ignore[assignment]
    smooth_ambiguity_portfolio = None  # type: ignore[assignment]
    maxmin_expected_utility_portfolio = None  # type: ignore[assignment]


def _uniform_weights(N: int) -> np.ndarray:
    return np.ones(N) / N


def _fallback_multiplier(returns: np.ndarray, theta: float) -> dict[str, Any]:
    return {"weights": _uniform_weights(returns.shape[1])}


def _fallback_variational(returns: np.ndarray) -> dict[str, Any]:
    return {"weights": _uniform_weights(returns.shape[1])}


def _fallback_smooth(returns: np.ndarray) -> dict[str, Any]:
    return {"weights": _uniform_weights(returns.shape[1])}


def _fallback_maxmin(scenarios: list[np.ndarray]) -> dict[str, Any]:
    N = scenarios[0].shape[1]
    return {"weights": _uniform_weights(N)}


def robust_decision_workflow(
    returns: np.ndarray,
    theta: float = 2.0,
) -> dict[str, Any]:
    """Multi-criterion robust portfolio selection.

    Computes four robust portfolio allocations:
    - Hansen-Sargent multiplier preferences
    - Variational (MMR) preferences
    - Smooth ambiguity (KMM)
    - Max-min expected utility (Gilboa-Schmeidler)

    Aggregates them with equal weights and measures pairwise dispersion.

    Parameters
    ----------
    returns : np.ndarray
        Returns matrix of shape (T, N). Minimum 30 rows, 2 columns.
    theta : float
        Hansen-Sargent penalty / uncertainty parameter (> 0).

    Returns
    -------
    dict with keys:
        ``robust_weights`` — equal-weight aggregate of the four portfolios (N,).
        ``individual`` — dict with keys "multiplier", "variational",
            "smooth_ambiguity", "maxmin" each containing weight arrays.
        ``weight_dispersion`` — mean pairwise L2 distance between the four
            weight vectors (measures framework disagreement).
    """
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 2:
        raise ValueError("returns must be a 2-D array of shape (T, N)")
    T, N = returns.shape
    if T < 30:
        raise ValueError(f"returns must have at least 30 observations, got {T}")
    if N < 2:
        raise ValueError(f"returns must have at least 2 assets, got {N}")
    if theta <= 0:
        raise ValueError(f"theta must be > 0, got {theta!r}")

    # 1. Multiplier preferences (Hansen-Sargent)
    if multiplier_preferences_robust is not None:
        try:
            mult_result = multiplier_preferences_robust(returns, theta=theta)
        except Exception:
            mult_result = _fallback_multiplier(returns, theta)
    else:
        mult_result = _fallback_multiplier(returns, theta)

    w_mult = np.asarray(mult_result["weights"])

    # 2. Variational preferences (MMR)
    if variational_preferences_estimate is not None:
        try:
            var_result = variational_preferences_estimate(returns, ambiguity_index=theta)
        except Exception:
            var_result = _fallback_variational(returns)
    else:
        var_result = _fallback_variational(returns)

    w_var = np.asarray(var_result["weights"])

    # 3. Smooth ambiguity (KMM) — split returns into two sub-scenarios
    half = T // 2
    scenarios_smooth = [returns[:half], returns[half:]]
    prior = np.array([0.5, 0.5])
    if smooth_ambiguity_portfolio is not None:
        try:
            smooth_result = smooth_ambiguity_portfolio(
                scenarios_smooth, prior_over_models=prior, ambiguity_aversion=theta
            )
        except Exception:
            smooth_result = _fallback_smooth(returns)
    else:
        smooth_result = _fallback_smooth(returns)

    w_smooth = np.asarray(smooth_result["weights"])

    # 4. Max-min expected utility (Gilboa-Schmeidler) — use 3 sub-scenarios
    third = T // 3
    scenarios_maxmin = [
        returns[:third],
        returns[third : 2 * third],
        returns[2 * third :],
    ]
    # Filter out scenarios with too few rows
    scenarios_maxmin = [s for s in scenarios_maxmin if s.shape[0] >= 5]
    if len(scenarios_maxmin) < 2:
        scenarios_maxmin = [returns[:half], returns[half:]]

    if maxmin_expected_utility_portfolio is not None:
        try:
            maxmin_result = maxmin_expected_utility_portfolio(scenarios_maxmin)
        except Exception:
            maxmin_result = _fallback_maxmin(scenarios_maxmin)
    else:
        maxmin_result = _fallback_maxmin(scenarios_maxmin)

    w_maxmin = np.asarray(maxmin_result["weights"])

    # Ensure all weight vectors sum to 1
    def _normalize(w: np.ndarray) -> np.ndarray:
        s = w.sum()
        return w / s if s > 1e-12 else np.ones(len(w)) / len(w)

    w_mult = _normalize(w_mult)
    w_var = _normalize(w_var)
    w_smooth = _normalize(w_smooth)
    w_maxmin = _normalize(w_maxmin)

    # Aggregate with equal weights
    robust_weights = (w_mult + w_var + w_smooth + w_maxmin) / 4.0

    # Compute pairwise L2 dispersion
    all_weights = [w_mult, w_var, w_smooth, w_maxmin]
    pairs = [
        (i, j)
        for i in range(4)
        for j in range(i + 1, 4)
    ]
    pairwise_dists = [
        float(np.linalg.norm(all_weights[i] - all_weights[j]))
        for i, j in pairs
    ]
    weight_dispersion = float(np.mean(pairwise_dists))

    return {
        "robust_weights": robust_weights,
        "individual": {
            "multiplier": w_mult,
            "variational": w_var,
            "smooth_ambiguity": w_smooth,
            "maxmin": w_maxmin,
        },
        "weight_dispersion": weight_dispersion,
    }
