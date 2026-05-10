"""Group 4: Signal & Alert modules."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

import oprim
import oskill


def alert_calibration_engine(
    alerts_history: pd.DataFrame,
    *,
    group_by: list[str] | None = None,
    n_bins: int = 10,
    include_bandit_state: bool = True,
    bandit_prior_alpha: float = 1.0,
    bandit_prior_beta: float = 1.0,
    time_window: pd.Timedelta | None = None,
) -> dict:
    """Alert system calibration with Bandit feedback.

    Calls:
        oskill.calibration_analysis, oprim.bayes_beta_update, oprim.brier_score_decomposed
    """
    if group_by is None:
        group_by = ["alert_type"]

    required = {"predicted_prob", "actual_outcome"}
    if not required.issubset(alerts_history.columns):
        raise ValueError(f"alerts_history must have columns: {required}")
    if len(alerts_history) == 0:
        raise ValueError("alerts_history must not be empty")

    df = alerts_history.copy()
    if time_window is not None and "ts" in df.columns:
        latest = pd.to_datetime(df["ts"]).max()
        df = df[pd.to_datetime(df["ts"]) >= latest - time_window]

    preds = df["predicted_prob"].values.astype(float)
    outcomes = df["actual_outcome"].values.astype(float)

    # Overall calibration
    overall = oskill.calibration_analysis(preds, outcomes, n_bins=n_bins)

    # Per-group calibration
    per_group = {}
    valid_groups = [g for g in group_by if g in df.columns]
    if valid_groups:
        group_col = valid_groups[0]
        for group_name, group_df in df.groupby(group_col):
            g_preds = group_df["predicted_prob"].values.astype(float)
            g_outcomes = group_df["actual_outcome"].values.astype(float)
            if len(g_preds) < 5:
                continue
            g_cal = oskill.calibration_analysis(g_preds, g_outcomes, n_bins=min(n_bins, len(g_preds) // 2))

            bandit_state = None
            if include_bandit_state:
                successes = int(g_outcomes.sum())
                failures = len(g_outcomes) - successes
                bandit_state = oprim.bayes_beta_update(
                    bandit_prior_alpha, bandit_prior_beta,
                    successes=successes, failures=failures,
                )
                bandit_state["n_observed"] = len(g_outcomes)

            per_group[str(group_name)] = {"calibration": g_cal, "bandit_state": bandit_state}

    # Summary
    group_eces = {k: v["calibration"]["ece"] for k, v in per_group.items()}
    best = min(group_eces, key=group_eces.get) if group_eces else None
    worst = max(group_eces, key=group_eces.get) if group_eces else None

    return {
        "overall": overall,
        "per_group": per_group,
        "summary": {
            "n_alerts_total": len(df),
            "n_groups": len(per_group),
            "best_calibrated_group": best,
            "worst_calibrated_group": worst,
        },
        "warnings": [],
    }


def thesis_invalidation_monitor(
    thesis_history: pd.DataFrame,
    *,
    rolling_window: int = 30,
    brier_threshold: float = 0.25,
    include_trend_analysis: bool = True,
    mk_alpha: float = 0.05,
    group_by: str = "thesis_id",
) -> dict:
    """Monitor thesis validity with 4-state judgment.

    Calls:
        oskill.calibration_analysis, oprim.mann_kendall_trend, oprim.brier_score_decomposed
    """
    required = {"predicted_prob", "actual_outcome"}
    if not required.issubset(thesis_history.columns):
        raise ValueError(f"thesis_history must have columns: {required}")
    if group_by not in thesis_history.columns:
        raise ValueError(f"group_by column '{group_by}' not in thesis_history")
    if len(thesis_history) == 0:
        raise ValueError("thesis_history must not be empty")

    per_thesis = {}
    for thesis_id, group_df in thesis_history.groupby(group_by):
        preds = group_df["predicted_prob"].values.astype(float)
        outcomes = group_df["actual_outcome"].values.astype(float)

        if len(preds) < 5:
            continue

        # Latest Brier score
        brier = oprim.brier_score_decomposed(preds, outcomes)
        latest_brier = brier["brier_score"]

        # Rolling Brier
        rolling_briers = []
        for i in range(rolling_window, len(preds) + 1):
            w_preds = preds[i - rolling_window:i]
            w_out = outcomes[i - rolling_window:i]
            rb = oprim.brier_score_decomposed(w_preds, w_out)
            rolling_briers.append(rb["brier_score"])

        # Trend analysis
        trend_test = None
        trend_increasing = False
        if include_trend_analysis and len(rolling_briers) > 10:
            trend_test = oprim.mann_kendall_trend(np.array(rolling_briers))
            trend_increasing = (trend_test["p_value"] < mk_alpha and
                                trend_test.get("trend", "") in ("increasing", "up"))

        # 4-state judgment
        above_threshold = latest_brier > brier_threshold
        if above_threshold and trend_increasing:
            status = "INVALIDATED"
        elif above_threshold:
            status = "AT_RISK"
        elif trend_increasing:
            status = "WARNING"
        else:
            status = "VALID"

        # Calibration
        n_bins = min(10, len(preds) // 3)
        cal = oskill.calibration_analysis(preds, outcomes, n_bins=max(2, n_bins)) if len(preds) >= 10 else None

        per_thesis[str(thesis_id)] = {
            "status": status,
            "latest_brier": float(latest_brier),
            "rolling_brier": rolling_briers,
            "trend_test": trend_test,
            "calibration": cal,
            "alert_message": f"Thesis {thesis_id}: {status} (Brier={latest_brier:.3f})",
        }

    # Summary
    statuses = [v["status"] for v in per_thesis.values()]
    return {
        "per_thesis": per_thesis,
        "summary": {
            "n_thesis": len(per_thesis),
            "n_valid": statuses.count("VALID"),
            "n_warning": statuses.count("WARNING"),
            "n_at_risk": statuses.count("AT_RISK"),
            "n_invalidated": statuses.count("INVALIDATED"),
            "invalidated_thesis_ids": [k for k, v in per_thesis.items() if v["status"] == "INVALIDATED"],
        },
        "warnings": [],
    }
