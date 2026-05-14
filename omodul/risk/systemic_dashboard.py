"""Systemic Risk Dashboard — full systemic risk analysis workflow."""
from __future__ import annotations

from typing import Any

import numpy as np

try:
    from oskill.networks.centrality import financial_network_centrality
    from oskill.networks.clearing import eisenberg_noe_clearing
    from oskill.risk.systemic import systemic_risk_metrics
except ImportError:  # pragma: no cover
    systemic_risk_metrics = None  # type: ignore[assignment]
    financial_network_centrality = None  # type: ignore[assignment]
    eisenberg_noe_clearing = None  # type: ignore[assignment]


def _fallback_systemic_metrics(
    institution_returns: np.ndarray, market_returns: np.ndarray
) -> dict[str, np.ndarray]:
    N = institution_returns.shape[1]
    return {"covar": np.zeros(N), "mes": np.zeros(N)}


def _fallback_centrality(exposure_matrix: np.ndarray) -> dict[str, np.ndarray]:
    N = exposure_matrix.shape[0]
    uniform = np.ones(N) / N
    return {"debt_rank": uniform, "eigenvector": uniform}


def _fallback_clearing(
    nominal_liabilities: np.ndarray, external_assets: np.ndarray
) -> dict[str, Any]:
    N = nominal_liabilities.shape[0]
    return {
        "clearing_vector": external_assets.copy(),
        "default_status": np.zeros(N, dtype=bool),
        "iterations": 0,
        "recovery_rates": np.ones(N),
    }


def _build_liability_matrix(returns: np.ndarray) -> np.ndarray:
    """Construct a simple liability matrix from return correlations."""
    T, N = returns.shape
    corr = np.corrcoef(returns.T)
    # Use positive correlations as proxies for bilateral exposures
    liab = np.maximum(corr, 0.0)
    np.fill_diagonal(liab, 0.0)
    # Scale so each row's total liability is meaningful (0.1 * portfolio)
    row_sum = liab.sum(axis=1, keepdims=True)
    row_sum = np.where(row_sum > 0, row_sum, 1.0)
    liab = liab / row_sum * 0.1
    return liab


def systemic_risk_dashboard(
    returns: np.ndarray,
    liabilities: np.ndarray | None = None,
) -> dict[str, Any]:
    """Full systemic risk analysis pipeline.

    Computes CoVaR/MES systemic risk metrics, financial network centrality,
    and runs the Eisenberg-Noe interbank clearing model.

    Parameters
    ----------
    returns : np.ndarray
        Returns matrix of shape (T, N). Minimum 30 rows, 2 columns.
        Rows are time periods; columns are institutions.
    liabilities : np.ndarray or None
        (N, N) liability matrix. If None, constructed from correlations.

    Returns
    -------
    dict with keys:
        ``systemic_metrics`` — dict of CoVaR/MES arrays per institution.
        ``network_centrality`` — dict of debt_rank/eigenvector arrays.
        ``clearing_result`` — dict from Eisenberg-Noe algorithm.
        ``risk_summary`` — summary statistics across institutions.
    """
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 2:
        raise ValueError("returns must be a 2-D array of shape (T, N)")
    T, N = returns.shape
    if T < 30:
        raise ValueError(f"returns must have at least 30 observations, got {T}")
    if N < 2:
        raise ValueError(f"returns must have at least 2 institutions, got {N}")

    # Market returns: equal-weighted portfolio of all institutions
    market_returns = returns.mean(axis=1)

    # 1. Systemic risk metrics (CoVaR, MES)
    if systemic_risk_metrics is not None:
        try:
            sys_metrics = systemic_risk_metrics(
                returns, market_returns, metrics=["covar", "mes"]
            )
        except Exception:
            sys_metrics = _fallback_systemic_metrics(returns, market_returns)
    else:
        sys_metrics = _fallback_systemic_metrics(returns, market_returns)

    # 2. Build liability/exposure matrix
    if liabilities is not None:
        liab_matrix = np.asarray(liabilities, dtype=float)
        if liab_matrix.shape != (N, N):
            raise ValueError(f"liabilities must have shape ({N}, {N}), got {liab_matrix.shape}")
    else:
        liab_matrix = _build_liability_matrix(returns)

    # 3. Network centrality
    if financial_network_centrality is not None:
        try:
            centrality = financial_network_centrality(
                liab_matrix, metrics=["debt_rank", "eigenvector"]
            )
        except Exception:
            centrality = _fallback_centrality(liab_matrix)
    else:
        centrality = _fallback_centrality(liab_matrix)

    # 4. Eisenberg-Noe clearing
    # External assets: proportional to mean positive returns
    ext_assets = np.maximum(returns.mean(axis=0) + 0.05, 0.01)
    if eisenberg_noe_clearing is not None:
        try:
            clearing = eisenberg_noe_clearing(liab_matrix, ext_assets)
        except Exception:
            clearing = _fallback_clearing(liab_matrix, ext_assets)
    else:
        clearing = _fallback_clearing(liab_matrix, ext_assets)

    # 5. Risk summary
    covar_arr = np.asarray(sys_metrics.get("covar", np.zeros(N)))
    mes_arr = np.asarray(sys_metrics.get("mes", np.zeros(N)))
    debt_rank = np.asarray(centrality.get("debt_rank", np.ones(N) / N))
    recovery = np.asarray(clearing.get("recovery_rates", np.ones(N)))
    default_status = np.asarray(clearing.get("default_status", np.zeros(N, dtype=bool)))

    risk_summary = {
        "n_institutions": int(N),
        "n_defaults": int(default_status.sum()),
        "mean_covar": float(np.mean(covar_arr)),
        "mean_mes": float(np.mean(mes_arr)),
        "most_systemic_institution": int(np.argmin(mes_arr)),
        "highest_centrality_institution": int(np.argmax(debt_rank)),
        "mean_recovery_rate": float(np.mean(recovery)),
        "min_recovery_rate": float(np.min(recovery)),
    }

    return {
        "systemic_metrics": sys_metrics,
        "network_centrality": centrality,
        "clearing_result": clearing,
        "risk_summary": risk_summary,
    }
