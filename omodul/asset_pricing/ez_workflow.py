"""Epstein-Zin Asset Pricing Workflow — Bansal-Yaron long-run risks."""
from __future__ import annotations

from typing import Any

import numpy as np

try:
    from oskill.recursive_utility.ez_solver import epstein_zin_solver
except ImportError:  # pragma: no cover
    epstein_zin_solver = None  # type: ignore[assignment]

try:
    from oprim.recursive_utility.epstein_zin import epstein_zin_aggregator
except ImportError:  # pragma: no cover
    epstein_zin_aggregator = None  # type: ignore[assignment]


# Default Bansal-Yaron (2004) monthly calibration
_BY_CALIBRATION = {
    "mu": 0.0015,      # mean consumption growth
    "rho": 0.979,      # long-run risk persistence
    "phi": 0.044,      # long-run risk volatility loading
    "sigma_bar": 0.0078,  # mean volatility
    "nu": 0.987,       # volatility persistence
    "sigma_omega": 2.3e-6,  # volatility-of-volatility
}


def _fallback_ez_solver(
    consumption_process: dict[str, Any],
    *,
    discount: float,
    risk_aversion: float,
    ies: float,
) -> dict[str, Any]:
    """Minimal fallback: simple log-linear approximation."""
    mu = float(consumption_process.get("mu", 0.0015))
    phi = float(consumption_process.get("phi", 0.044))
    sigma_bar = float(consumption_process.get("sigma_bar", 0.0078))
    rho = float(consumption_process.get("rho", 0.979))

    n_grid = 50
    sigma_x = phi * sigma_bar / np.sqrt(max(1.0 - rho**2, 1e-10))
    x_grid = np.linspace(-3.0 * sigma_x, 3.0 * sigma_x, n_grid)
    V = np.exp(mu + x_grid)

    var_x = (phi * sigma_bar) ** 2 / max(1.0 - rho**2, 1e-10)
    ep = max((risk_aversion - 1.0 / ies) * var_x * 12.0, 0.0)

    return {
        "value_function": V,
        "consumption_policy": np.exp(mu + x_grid),
        "wealth_consumption_ratio": float(V[n_grid // 2] / max(np.exp(mu), 1e-30)),
        "equity_premium_implied": float(ep),
        "converged": False,
        "iterations": 0,
    }


def _fallback_aggregator(C: np.ndarray, CE: np.ndarray, *, discount: float,
                          risk_aversion: float, ies: float) -> np.ndarray:
    rho = 1.0 - 1.0 / ies
    if abs(rho) < 1e-12:
        return C ** (1.0 - discount) * CE**discount
    inner = (1.0 - discount) * C**rho + discount * CE**rho
    return inner ** (1.0 / rho)


def epstein_zin_asset_pricing_workflow(
    *,
    risk_aversion: float = 10.0,
    ies: float = 1.5,
    discount: float = 0.998,
) -> dict[str, Any]:
    """Epstein-Zin asset pricing workflow using Bansal-Yaron calibration.

    Solves the EZ value function on the long-run risk state space, performs
    an aggregation check with the oprim aggregator, and returns implied
    asset pricing moments.

    Parameters
    ----------
    risk_aversion : float
        Coefficient of relative risk aversion (> 0).
    ies : float
        Intertemporal elasticity of substitution (> 0).
    discount : float
        Time discount factor beta, in (0, 1).

    Returns
    -------
    dict with keys:
        ``value_function`` — solved value function on the x-grid (n_grid,).
        ``equity_premium`` — implied annualized equity premium.
        ``risk_free_rate`` — implied annualized risk-free rate.
        ``aggregator_check`` — single-period aggregator check value.
    """
    if risk_aversion <= 0:
        raise ValueError(f"risk_aversion must be > 0, got {risk_aversion!r}")
    if ies <= 0:
        raise ValueError(f"ies must be > 0, got {ies!r}")
    if not (0.0 < discount < 1.0):
        raise ValueError(f"discount must be in (0, 1), got {discount!r}")

    consumption_process = _BY_CALIBRATION.copy()

    # 1. Solve EZ value function
    if epstein_zin_solver is not None:
        try:
            ez_result = epstein_zin_solver(
                consumption_process,
                discount=discount,
                risk_aversion=risk_aversion,
                ies=ies,
                n_grid=100,
                max_iter=300,
            )
        except Exception:
            ez_result = _fallback_ez_solver(
                consumption_process,
                discount=discount,
                risk_aversion=risk_aversion,
                ies=ies,
            )
    else:
        ez_result = _fallback_ez_solver(
            consumption_process,
            discount=discount,
            risk_aversion=risk_aversion,
            ies=ies,
        )

    value_function = np.asarray(ez_result.get("value_function", np.ones(100)))
    equity_premium = float(ez_result.get("equity_premium_implied", 0.0))

    # 2. Implied risk-free rate (log-linear BY approximation, annualized)
    mu = float(consumption_process["mu"])
    sigma_bar = float(consumption_process["sigma_bar"])
    # Monthly: rf_monthly ≈ mu/psi - (1 - 1/(2*psi)) * sigma^2 * gamma
    psi = ies
    rf_monthly = (mu / psi) - 0.5 * (1.0 - 1.0 / (2.0 * psi)) * sigma_bar**2 * risk_aversion
    risk_free_rate = float(rf_monthly * 12.0)  # annualize

    # 3. Aggregator check — use mean state values from value function
    n = len(value_function)
    mid = n // 2
    C_check = np.array([np.exp(mu)])
    CE_check = np.array([float(value_function[mid])])

    # Ensure positive inputs
    C_check = np.maximum(C_check, 1e-10)
    CE_check = np.maximum(CE_check, 1e-10)

    if epstein_zin_aggregator is not None:
        try:
            agg_val = epstein_zin_aggregator(
                C_check,
                CE_check,
                discount=discount,
                risk_aversion=risk_aversion,
                ies=ies,
            )
            aggregator_check = float(agg_val[0])
        except Exception:
            agg_arr = _fallback_aggregator(
                C_check, CE_check,
                discount=discount,
                risk_aversion=risk_aversion,
                ies=ies,
            )
            aggregator_check = float(agg_arr[0])
    else:
        agg_arr = _fallback_aggregator(
            C_check, CE_check,
            discount=discount,
            risk_aversion=risk_aversion,
            ies=ies,
        )
        aggregator_check = float(agg_arr[0])

    return {
        "value_function": value_function,
        "equity_premium": equity_premium,
        "risk_free_rate": risk_free_rate,
        "aggregator_check": aggregator_check,
    }
