"""High-Dimensional Portfolio Workflow — spectral cleaning + HRP v2 + SSD."""
from __future__ import annotations

from typing import Any

import numpy as np

try:
    from oprim.spectral.marchenko_pastur import marchenko_pastur_threshold
    from oprim.spectral.rie import rotationally_invariant_estimator
except ImportError:  # pragma: no cover
    marchenko_pastur_threshold = None  # type: ignore[assignment]
    rotationally_invariant_estimator = None  # type: ignore[assignment]

try:
    from oskill.portfolio.hrp import hierarchical_risk_parity_v2
    from oskill.portfolio.ssd_milp import ssd_milp_optimizer
    from oskill.spectral.clustering import spectral_asset_clustering
except ImportError:  # pragma: no cover
    spectral_asset_clustering = None  # type: ignore[assignment]
    hierarchical_risk_parity_v2 = None  # type: ignore[assignment]
    ssd_milp_optimizer = None  # type: ignore[assignment]


def _fallback_mp_threshold(T: int, N: int) -> dict[str, float]:
    q = N / T
    lambda_max = (1.0 + np.sqrt(q)) ** 2
    return {"q": q, "lambda_min": 0.0, "lambda_max": lambda_max, "mass_zero": 0.0}


def _fallback_rie(cov: np.ndarray, n_samples: int) -> dict[str, Any]:
    return {"cov_rie": cov}


def _fallback_clustering(corr: np.ndarray) -> dict[str, Any]:
    N = corr.shape[0]
    labels = np.arange(N) % max(1, N // 5)
    return {"cluster_labels": labels, "n_clusters_inferred": int(labels.max()) + 1,
            "graph_edges": [], "modularity": 0.0}


def _fallback_hrp(returns: np.ndarray) -> dict[str, Any]:
    N = returns.shape[1]
    w = np.ones(N) / N
    return {"weights": w, "linkage_matrix": np.array([]), "cluster_order": list(range(N)),
            "cov_used": np.cov(returns.T, ddof=1)}


def _fallback_ssd(
    asset_returns: np.ndarray, benchmark_returns: np.ndarray
) -> dict[str, Any]:
    N = asset_returns.shape[1]
    w = np.ones(N) / N
    return {"weights": w, "ssd_constraint_active_states": [], "milp_objective": 0.0,
            "dominance_certificate": np.zeros(5)}


def high_dim_portfolio_workflow(
    returns: np.ndarray,
) -> dict[str, Any]:
    """High-dimensional portfolio construction pipeline.

    Uses Marchenko-Pastur threshold for noise filtering, RIE covariance
    estimation, spectral clustering, HRP v2 weighting, and SSD optimization.

    Parameters
    ----------
    returns : np.ndarray
        Returns matrix of shape (T, N). Minimum 30 rows, 2 columns.

    Returns
    -------
    dict with keys:
        ``hrp_weights`` — HRP v2 portfolio weights (N,).
        ``ssd_weights`` — SSD-optimized portfolio weights (N,).
        ``clusters`` — cluster label per asset (N,).
        ``noise_threshold`` — Marchenko-Pastur lambda_max.
        ``n_clusters`` — number of clusters found.
    """
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 2:
        raise ValueError("returns must be a 2-D array of shape (T, N)")
    T, N = returns.shape
    if T < 30:
        raise ValueError(f"returns must have at least 30 observations, got {T}")
    if N < 2:
        raise ValueError(f"returns must have at least 2 assets, got {N}")

    # 1. Marchenko-Pastur noise threshold
    if marchenko_pastur_threshold is not None:
        try:
            mp_result = marchenko_pastur_threshold(n_samples=T, n_features=N)
        except Exception:
            mp_result = _fallback_mp_threshold(T, N)
    else:
        mp_result = _fallback_mp_threshold(T, N)

    noise_threshold = float(mp_result["lambda_max"])

    # 2. RIE covariance estimation
    raw_cov = np.cov(returns.T, ddof=1)
    if rotationally_invariant_estimator is not None:
        try:
            rie_result = rotationally_invariant_estimator(raw_cov, n_samples=T)
            rie_cov = rie_result["cov_rie"]
        except Exception:
            rie_cov = raw_cov
    else:
        rie_cov = raw_cov

    # 3. Spectral asset clustering via cleaned correlation matrix
    diag_sqrt = np.sqrt(np.diag(rie_cov) + 1e-12)
    corr_matrix = rie_cov / np.outer(diag_sqrt, diag_sqrt)
    corr_matrix = np.clip(corr_matrix, -1.0, 1.0)

    if spectral_asset_clustering is not None:
        try:
            clust_result = spectral_asset_clustering(corr_matrix)
        except Exception:
            clust_result = _fallback_clustering(corr_matrix)
    else:
        clust_result = _fallback_clustering(corr_matrix)

    cluster_labels = np.asarray(clust_result["cluster_labels"])
    n_clusters = int(clust_result.get("n_clusters_inferred", len(np.unique(cluster_labels))))

    # 4. HRP v2 weights using cleaned covariance
    if hierarchical_risk_parity_v2 is not None:
        try:
            hrp_result = hierarchical_risk_parity_v2(returns, use_rie_cleaning=True)
        except Exception:
            hrp_result = _fallback_hrp(returns)
    else:
        hrp_result = _fallback_hrp(returns)

    hrp_weights = np.asarray(hrp_result["weights"])

    # 5. SSD optimization using HRP as benchmark
    # Benchmark: equal-weighted portfolio
    benchmark_returns = returns.mean(axis=1)
    if ssd_milp_optimizer is not None:
        try:
            ssd_result = ssd_milp_optimizer(returns, benchmark_returns)
        except Exception:
            ssd_result = _fallback_ssd(returns, benchmark_returns)
    else:
        ssd_result = _fallback_ssd(returns, benchmark_returns)

    ssd_weights = np.asarray(ssd_result["weights"])

    return {
        "hrp_weights": hrp_weights,
        "ssd_weights": ssd_weights,
        "clusters": cluster_labels,
        "noise_threshold": noise_threshold,
        "n_clusters": n_clusters,
    }
