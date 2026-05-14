"""Cross-Framework Benchmark Report — compare EU, CPT, Robust, and Salience."""
from __future__ import annotations

from typing import Any

import numpy as np

try:
    from oskill.behavioral.cpt_portfolio import cpt_portfolio_optimize
    from oskill.behavioral.salience_pricing import salience_asset_pricing
    from oskill.robust.multiplier_preferences import multiplier_preferences_robust
except ImportError:  # pragma: no cover
    cpt_portfolio_optimize = None  # type: ignore[assignment]
    multiplier_preferences_robust = None  # type: ignore[assignment]
    salience_asset_pricing = None  # type: ignore[assignment]


def _sharpe(returns: np.ndarray) -> float:
    """Annualized Sharpe ratio (assumes daily returns)."""
    mu = float(np.mean(returns))
    std = float(np.std(returns, ddof=1))
    return (mu / std * np.sqrt(252)) if std > 1e-12 else 0.0


def _max_drawdown(returns: np.ndarray) -> float:
    """Maximum drawdown from a returns series."""
    cum = np.cumprod(1.0 + returns)
    running_max = np.maximum.accumulate(cum)
    drawdown = (cum - running_max) / np.maximum(running_max, 1e-12)
    return float(np.min(drawdown))


def _mv_weights(returns: np.ndarray) -> np.ndarray:
    """Mean-variance (equal-weighted as EU proxy)."""
    N = returns.shape[1]
    return np.ones(N) / N


def _fallback_cpt(returns: np.ndarray) -> np.ndarray:
    return _mv_weights(returns)


def _fallback_robust(returns: np.ndarray) -> np.ndarray:
    return _mv_weights(returns)


def cross_framework_benchmark_report(
    returns: np.ndarray,
) -> dict[str, Any]:
    """Compare CPT vs EU vs Robust vs Salience portfolio frameworks.

    Computes weights under four decision-theoretic frameworks, evaluates
    performance metrics (Sharpe, max drawdown), and recommends the best
    framework for the given dataset.

    Parameters
    ----------
    returns : np.ndarray
        Returns matrix of shape (T, N). Minimum 30 rows, 2 columns.

    Returns
    -------
    dict with keys:
        ``frameworks`` — per-framework results dict (weights, Sharpe, drawdown).
        ``summary_table`` — list of dicts summarizing each framework.
        ``recommended_framework`` — name of the framework with best Sharpe.
    """
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 2:
        raise ValueError("returns must be a 2-D array of shape (T, N)")
    T, N = returns.shape
    if T < 30:
        raise ValueError(f"returns must have at least 30 observations, got {T}")
    if N < 2:
        raise ValueError(f"returns must have at least 2 assets, got {N}")

    results: dict[str, dict[str, Any]] = {}

    # 1. EU: equal-weighted mean-variance proxy
    w_eu = _mv_weights(returns)
    r_eu = returns @ w_eu
    results["eu"] = {
        "weights": w_eu,
        "portfolio_returns": r_eu,
        "sharpe": _sharpe(r_eu),
        "max_drawdown": _max_drawdown(r_eu),
    }

    # 2. CPT optimization
    if cpt_portfolio_optimize is not None:
        try:
            cpt_res = cpt_portfolio_optimize(returns)
            w_cpt = np.asarray(cpt_res["weights"])
        except Exception:
            w_cpt = _fallback_cpt(returns)
    else:
        w_cpt = _fallback_cpt(returns)

    w_cpt = np.maximum(w_cpt, 0.0)
    s = w_cpt.sum()
    w_cpt = w_cpt / s if s > 1e-12 else _mv_weights(returns)
    r_cpt = returns @ w_cpt
    results["cpt"] = {
        "weights": w_cpt,
        "portfolio_returns": r_cpt,
        "sharpe": _sharpe(r_cpt),
        "max_drawdown": _max_drawdown(r_cpt),
    }

    # 3. Robust (Hansen-Sargent multiplier)
    if multiplier_preferences_robust is not None:
        try:
            rob_res = multiplier_preferences_robust(returns, theta=2.0)
            w_rob = np.asarray(rob_res["weights"])
        except Exception:
            w_rob = _fallback_robust(returns)
    else:
        w_rob = _fallback_robust(returns)

    w_rob = np.maximum(w_rob, 0.0)
    s = w_rob.sum()
    w_rob = w_rob / s if s > 1e-12 else _mv_weights(returns)
    r_rob = returns @ w_rob
    results["robust"] = {
        "weights": w_rob,
        "portfolio_returns": r_rob,
        "sharpe": _sharpe(r_rob),
        "max_drawdown": _max_drawdown(r_rob),
    }

    # 4. Salience-based portfolio — apply salience pricing to EU weights as benchmark
    # Use equal-weighted portfolio payoffs as market, each asset as individual payoff
    market_payoffs = returns @ w_eu  # (T,)
    if salience_asset_pricing is not None:
        try:
            sal_res = salience_asset_pricing(
                returns.T,     # (N, T): N assets, T states
                market_payoffs,  # (T,)
            )
            salient_prices = np.asarray(sal_res["salient_price"])
            # Positive prices → long; normalize
            w_sal = np.maximum(salient_prices, 0.0)
            s = w_sal.sum()
            w_sal = w_sal / s if s > 1e-12 else _mv_weights(returns)
        except Exception:
            w_sal = _mv_weights(returns)
    else:
        w_sal = _mv_weights(returns)

    r_sal = returns @ w_sal
    results["salience"] = {
        "weights": w_sal,
        "portfolio_returns": r_sal,
        "sharpe": _sharpe(r_sal),
        "max_drawdown": _max_drawdown(r_sal),
    }

    # Build summary table
    summary_table = [
        {
            "framework": name,
            "sharpe": float(results[name]["sharpe"]),
            "max_drawdown": float(results[name]["max_drawdown"]),
            "mean_return": float(np.mean(results[name]["portfolio_returns"])),
            "volatility": float(np.std(results[name]["portfolio_returns"], ddof=1)),
        }
        for name in ["eu", "cpt", "robust", "salience"]
    ]

    # Recommend best Sharpe
    best_row = max(summary_table, key=lambda r: r["sharpe"])
    recommended_framework = best_row["framework"]

    return {
        "frameworks": {
            name: {
                "weights": results[name]["weights"],
                "sharpe": results[name]["sharpe"],
                "max_drawdown": results[name]["max_drawdown"],
            }
            for name in ["eu", "cpt", "robust", "salience"]
        },
        "summary_table": summary_table,
        "recommended_framework": recommended_framework,
    }
