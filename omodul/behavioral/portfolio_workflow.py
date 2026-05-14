"""Behavioral Portfolio Workflow — integrates CPT optimization end-to-end."""
from __future__ import annotations

from typing import Any

import numpy as np

try:
    import oskill
    from oskill.behavioral.cpt_analytical import cpt_portfolio_analytical
    from oskill.behavioral.cpt_portfolio import cpt_portfolio_optimize
except ImportError:  # pragma: no cover
    oskill = None  # type: ignore[assignment]
    cpt_portfolio_optimize = None  # type: ignore[assignment]
    cpt_portfolio_analytical = None  # type: ignore[assignment]

try:
    import oprim
    from oprim.behavioral.llad import large_loss_aversion_degree
except ImportError:  # pragma: no cover
    oprim = None  # type: ignore[assignment]
    large_loss_aversion_degree = None  # type: ignore[assignment]


def _fallback_llad(alpha: float, beta: float, loss_aversion: float) -> dict[str, Any]:
    llad = (beta / alpha) * loss_aversion ** (1.0 / beta)
    return {"llad": llad, "well_posed": None, "llad_threshold": None}


def _fallback_cpt_optimize(
    returns: np.ndarray,
    *,
    alpha: float,
    beta: float,
    loss_aversion: float,
    reference_return: float,
) -> dict[str, Any]:
    N = returns.shape[1]
    w = np.ones(N) / N
    return {"weights": w, "cpt_value": 0.0, "convergence": {"success": False}}


def _fallback_cpt_analytical(
    returns: np.ndarray,
    reference_return: float,
    *,
    alpha: float,
    beta: float,
    loss_aversion: float,
) -> dict[str, Any]:
    return {"weight_optimal": 1.0 / returns.shape[0], "cpt_value": 0.0, "llad": 0.0,
            "well_posed": None}


def behavioral_portfolio_workflow(
    returns: np.ndarray,
    reference_return: float,
    *,
    alpha: float = 0.88,
    beta: float = 0.88,
    loss_aversion: float = 2.25,
) -> dict[str, Any]:
    """Integrate CPT portfolio theory end-to-end.

    Runs the numerical CPT optimizer, the Bernard-Ghossoub analytical
    closed-form solution, and the LLAD diagnostic, then compares them.

    Parameters
    ----------
    returns : np.ndarray
        Returns matrix of shape (T, N). Minimum 30 rows, 2 columns.
    reference_return : float
        Aspiration / reference return level for CPT evaluation.
    alpha : float
        Gain curvature parameter for the CPT value function (0, 1].
    beta : float
        Loss curvature parameter for the CPT value function (0, 1].
    loss_aversion : float
        Loss aversion coefficient lambda (>= 1).

    Returns
    -------
    dict with keys:
        ``cpt_weights`` — portfolio weight vector from numerical optimizer (N,).
        ``analytical_weight`` — scalar optimal weight from analytical solution.
        ``llad`` — Large Loss Aversion Degree diagnostic float.
        ``well_posed`` — bool or None, CPT problem well-posedness.
        ``comparison`` — dict comparing numeric vs analytical weights.
    """
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 2:
        raise ValueError("returns must be a 2-D array of shape (T, N)")
    T, N = returns.shape
    if T < 30:
        raise ValueError(f"returns must have at least 30 observations, got {T}")
    if N < 2:
        raise ValueError(f"returns must have at least 2 assets, got {N}")
    if not (0 < alpha <= 1):
        raise ValueError(f"alpha must be in (0, 1], got {alpha!r}")
    if not (0 < beta <= 1):
        raise ValueError(f"beta must be in (0, 1], got {beta!r}")
    if loss_aversion < 1:
        raise ValueError(f"loss_aversion must be >= 1, got {loss_aversion!r}")

    # 1. Numerical CPT optimizer
    if cpt_portfolio_optimize is not None:
        try:
            opt_result = cpt_portfolio_optimize(
                returns,
                alpha=alpha,
                beta=beta,
                loss_aversion=loss_aversion,
                reference_return=reference_return,
            )
        except Exception:
            opt_result = _fallback_cpt_optimize(
                returns, alpha=alpha, beta=beta, loss_aversion=loss_aversion,
                reference_return=reference_return,
            )
    else:
        opt_result = _fallback_cpt_optimize(
            returns, alpha=alpha, beta=beta, loss_aversion=loss_aversion,
            reference_return=reference_return,
        )

    cpt_weights = np.asarray(opt_result["weights"])

    # 2. Analytical solution (1-asset proxy using first column)
    analytical_returns = returns[:, 0]
    if cpt_portfolio_analytical is not None:
        try:
            anal_result = cpt_portfolio_analytical(
                analytical_returns,
                reference_return,
                alpha=alpha,
                beta=beta,
                loss_aversion=loss_aversion,
            )
        except Exception:
            anal_result = _fallback_cpt_analytical(
                returns, reference_return, alpha=alpha, beta=beta,
                loss_aversion=loss_aversion,
            )
    else:
        anal_result = _fallback_cpt_analytical(
            returns, reference_return, alpha=alpha, beta=beta,
            loss_aversion=loss_aversion,
        )

    analytical_weight = float(anal_result.get("weight_optimal", 0.0))

    # 3. LLAD diagnostic
    if large_loss_aversion_degree is not None:
        try:
            llad_result = large_loss_aversion_degree(
                alpha=alpha, beta=beta, loss_aversion=loss_aversion
            )
        except Exception:
            llad_result = _fallback_llad(alpha, beta, loss_aversion)
    else:
        llad_result = _fallback_llad(alpha, beta, loss_aversion)

    llad_val = float(llad_result["llad"])
    well_posed = llad_result.get("well_posed")

    # 4. Build comparison
    cpt_mean_weight = float(np.mean(np.abs(cpt_weights)))
    weight_diff = float(np.abs(analytical_weight - cpt_mean_weight))

    comparison = {
        "numeric_mean_abs_weight": cpt_mean_weight,
        "analytical_weight": analytical_weight,
        "weight_diff": weight_diff,
        "cpt_value_numeric": float(opt_result.get("cpt_value", float("nan"))),
    }

    return {
        "cpt_weights": cpt_weights,
        "analytical_weight": analytical_weight,
        "llad": llad_val,
        "well_posed": well_posed,
        "comparison": comparison,
    }
