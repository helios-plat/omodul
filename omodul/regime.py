"""Group 2: Regime / Market State Analysis modules."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

import oprim
import oskill


def regime_replay_search(
    current_panel: pd.DataFrame,
    historical_panels: list[dict],
    *,
    forward_days: int = 30,
    top_k: int = 10,
    methods: list[str] | None = None,
    ensemble: Literal["mean_rank", "borda", "weighted"] = "mean_rank",
    bootstrap_forward_ci: bool = True,
) -> dict:
    """Search historical analogues and project forward distribution.

    Calls:
        oskill.historical_analogy_search, oskill.regime_transition_analysis,
        oskill.bootstrap_distribution, oprim.regime_filter_data
    """
    if methods is None:
        methods = ["dtw", "wasserstein"]
    if len(historical_panels) == 0:
        raise ValueError("historical_panels must not be empty")
    if current_panel.empty:
        raise ValueError("current_panel must not be empty")

    # Extract time series from current panel (use first numeric column)
    query = current_panel.select_dtypes(include=[np.number]).iloc[:, 0].values

    # Build historical database
    db = [np.asarray(h["panel"].select_dtypes(include=[np.number]).iloc[:, 0].values)
          for h in historical_panels]

    # Use oskill.historical_analogy_search
    matches = oskill.historical_analogy_search(
        query, db, methods=methods, ensemble=ensemble, top_k=min(top_k, len(db))
    )

    # Extract forward returns from top matches
    forward_returns_list = []
    for match in matches:
        idx = match["historical_idx"]
        h = historical_panels[idx]
        if "forward_returns" in h and h["forward_returns"] is not None:
            fwd = np.asarray(h["forward_returns"])[:forward_days]
            if len(fwd) > 0:
                forward_returns_list.append(fwd)

    # Build forward distribution
    forward_dist = None
    cumulative_forward = {}
    if forward_returns_list:
        max_len = max(len(f) for f in forward_returns_list)
        # Pad shorter series with NaN
        padded = np.full((len(forward_returns_list), max_len), np.nan)
        for i, f in enumerate(forward_returns_list):
            padded[i, :len(f)] = f

        # Compute quantiles per day
        days = []
        for d in range(max_len):
            col = padded[:, d]
            valid = col[~np.isnan(col)]
            if len(valid) > 0:
                days.append({
                    "day": d + 1,
                    "q_05": float(np.percentile(valid, 5)),
                    "q_25": float(np.percentile(valid, 25)),
                    "q_50": float(np.percentile(valid, 50)),
                    "q_75": float(np.percentile(valid, 75)),
                    "q_95": float(np.percentile(valid, 95)),
                    "mean": float(np.mean(valid)),
                    "std": float(np.std(valid)),
                })
        forward_dist = pd.DataFrame(days)

        # Cumulative forward at horizon
        cum_returns = [float(np.prod(1 + padded[i, ~np.isnan(padded[i])]) - 1)
                       for i in range(len(forward_returns_list))]
        cum_arr = np.array(cum_returns)
        cumulative_forward = {
            "expected_return_at_horizon": float(np.mean(cum_arr)),
            "probability_positive": float((cum_arr > 0).mean()),
        }
        if bootstrap_forward_ci and len(cum_arr) > 3:
            boot = oskill.bootstrap_distribution(cum_arr, np.mean, n_bootstrap=500)
            cumulative_forward["ci_low"] = boot["ci_low"]
            cumulative_forward["ci_high"] = boot["ci_high"]

    # Regime transition summary
    regime_summary = None
    panels_with_regimes = [h for h in historical_panels if "regime_labels" in h and h["regime_labels"] is not None]
    if panels_with_regimes:
        first_labels = panels_with_regimes[0]["regime_labels"]
        if isinstance(first_labels, pd.Series) and len(first_labels) > 5:
            regime_summary = oskill.regime_transition_analysis(first_labels)

    return {
        "top_k_matches": matches,
        "forward_distribution": forward_dist,
        "cumulative_forward": cumulative_forward,
        "regime_transition_summary": regime_summary,
        "n_matches_used": len(forward_returns_list),
    }


def regime_change_detector(
    data: pd.DataFrame,
    regime_labels: pd.Series,
    *,
    window_before: int = 30,
    window_after: int = 30,
    metrics: list[str] | None = None,
    shift_test_methods: list[str] | None = None,
    include_transition_history: bool = True,
) -> dict:
    """Detect regime changes and analyze before/after differences.

    Calls:
        oskill.distribution_shift_test, oskill.regime_transition_analysis,
        oprim.regime_filter_data, oprim.sharpe_ratio
    """
    if metrics is None:
        metrics = ["sharpe", "vol", "skew"]
    if shift_test_methods is None:
        shift_test_methods = ["ks", "wasserstein"]

    if not isinstance(regime_labels, pd.Series):
        regime_labels = pd.Series(regime_labels)
    if len(data) != len(regime_labels):
        raise ValueError("data and regime_labels must have same length")
    if len(data) < window_before + window_after:
        raise ValueError("data too short for given window sizes")

    # Use first numeric column for analysis
    values = data.select_dtypes(include=[np.number]).iloc[:, 0]

    # Find transition points
    changes = regime_labels != regime_labels.shift(1)
    change_indices = changes[changes].index.tolist()

    transitions = []
    for idx in change_indices:
        pos = regime_labels.index.get_loc(idx)
        if pos < window_before or pos + window_after > len(values):
            continue

        from_regime = regime_labels.iloc[pos - 1]
        to_regime = regime_labels.iloc[pos]

        before = values.iloc[pos - window_before:pos].values
        after = values.iloc[pos:pos + window_after].values

        # Distribution shift test
        shift_result = oskill.distribution_shift_test(
            before, after, methods=shift_test_methods
        )

        # Compute metrics before/after
        metrics_before = {}
        metrics_after = {}
        for m in metrics:
            if m == "sharpe" and len(before) > 5:
                metrics_before[m] = float(oprim.sharpe_ratio(pd.Series(before)))
                metrics_after[m] = float(oprim.sharpe_ratio(pd.Series(after)))
            elif m == "vol":
                metrics_before[m] = float(np.std(before))
                metrics_after[m] = float(np.std(after))
            elif m == "skew":
                sk_b = oprim.skew_kurt_robust(before)
                sk_a = oprim.skew_kurt_robust(after)
                metrics_before[m] = sk_b["skewness"]
                metrics_after[m] = sk_a["skewness"]

        transitions.append({
            "timestamp": idx,
            "from_regime": from_regime,
            "to_regime": to_regime,
            "shift_detected": shift_result["shift_detected"],
            "metrics_before": metrics_before,
            "metrics_after": metrics_after,
            "metric_changes": {k: metrics_after.get(k, 0) - metrics_before.get(k, 0)
                               for k in metrics},
        })

    # Transition history
    transition_history = None
    if include_transition_history and len(regime_labels.dropna().unique()) >= 2:
        transition_history = oskill.regime_transition_analysis(regime_labels.dropna())

    return {
        "transitions": transitions,
        "n_transitions": len(transitions),
        "transition_history_summary": transition_history,
    }


def regime_conditional_dashboard_data(
    returns: pd.Series,
    regime_labels: pd.Series,
    *,
    metrics: list[str] | None = None,
    include_transitions: bool = True,
    include_pairwise_shift: bool = True,
    annualization_factor: float = 252.0,
) -> dict:
    """Generate regime-conditional dashboard data.

    Calls:
        oskill.regime_aware_performance, oskill.regime_transition_analysis,
        oskill.distribution_shift_test
    """
    if metrics is None:
        metrics = ["sharpe", "max_drawdown", "var_95", "cumulative_return"]

    if not isinstance(returns, pd.Series):
        returns = pd.Series(returns)
    if not isinstance(regime_labels, pd.Series):
        regime_labels = pd.Series(regime_labels, index=returns.index)

    if len(returns) != len(regime_labels):
        raise ValueError("returns and regime_labels must have same length")

    # Per-regime metrics using oskill
    per_regime = oskill.regime_aware_performance(
        returns, regime_labels, metrics=metrics,
        annualization_factor=annualization_factor,
    )

    # Transition analysis
    transition_analysis = None
    if include_transitions and len(regime_labels.dropna().unique()) >= 2:
        transition_analysis = oskill.regime_transition_analysis(regime_labels.dropna())

    # Pairwise shift matrix
    pairwise_shift = None
    if include_pairwise_shift:
        regimes = sorted(regime_labels.unique())
        if len(regimes) >= 2:
            shift_data = {}
            returns_df = pd.DataFrame({"returns": returns})
            for r1 in regimes:
                row = {}
                data_r1 = oprim.regime_filter_data(returns_df, regime_labels, r1)["returns"].values
                for r2 in regimes:
                    if r1 == r2:
                        row[r2] = False
                    else:
                        data_r2 = oprim.regime_filter_data(returns_df, regime_labels, r2)["returns"].values
                        if len(data_r1) > 10 and len(data_r2) > 10:
                            shift = oskill.distribution_shift_test(data_r1, data_r2)
                            row[r2] = shift["shift_detected"]
                        else:
                            row[r2] = None
                shift_data[r1] = row
            pairwise_shift = pd.DataFrame(shift_data).T

    regimes = sorted(regime_labels.unique())
    n_obs_per = {r: int((regime_labels == r).sum()) for r in regimes}

    return {
        "per_regime_metrics": per_regime,
        "transition_analysis": transition_analysis,
        "pairwise_shift_matrix": pairwise_shift,
        "summary": {
            "n_regimes": len(regimes),
            "regime_labels": regimes,
            "n_obs_per_regime": n_obs_per,
            "metrics_computed": metrics,
        },
    }
