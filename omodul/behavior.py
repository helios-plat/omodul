"""Group 1: Trading Behavior Analysis modules."""

from __future__ import annotations

from typing import Any, Callable, Literal, Optional

import numpy as np
import pandas as pd

import oprim
import oskill


def trade_journal_analyzer(
    trades: pd.DataFrame,
    *,
    diagnostics: list[str] | None = None,
    benchmark_returns: pd.Series | None = None,
    lookback_momentum: int = 5,
    bootstrap_ci: bool = True,
    n_bootstrap: int = 1000,
    random_state: int | None = None,
) -> dict:
    """Analyze trade journal for behavioral biases.

    Calls:
        oskill.detect_outliers_robust, oskill.bootstrap_distribution,
        oprim.percentile_rank, oprim.zscore_normalize

    Args:
        trades: DataFrame with columns: timestamp, symbol, side, quantity, price, pnl.
        diagnostics: Biases to check. Default: all 4.
        benchmark_returns: Optional benchmark for momentum analysis.
        lookback_momentum: Days for momentum correlation.
        bootstrap_ci: Whether to compute bootstrap CI.
        n_bootstrap: Bootstrap resamples.
        random_state: Random seed.

    Returns:
        Dict with diagnostics, behavior_metrics, summary_report.
    """
    if diagnostics is None:
        diagnostics = ["disposition", "overtrading", "chasing", "anchoring"]

    required_cols = {"timestamp", "symbol", "side", "quantity", "price"}
    if not required_cols.issubset(trades.columns):
        raise ValueError(f"trades must have columns: {required_cols}")
    if len(trades) == 0:
        raise ValueError("trades must not be empty")

    trades = trades.copy().sort_values("timestamp").reset_index(drop=True)
    results: dict = {}

    # Basic metrics
    n_trades = len(trades)
    has_pnl = "pnl" in trades.columns
    pnl = trades["pnl"].values if has_pnl else np.zeros(n_trades)
    wins = pnl > 0
    win_rate = float(wins.mean()) if has_pnl else np.nan

    # Disposition Effect (simplified - see docstring)
    if "disposition" in diagnostics and has_pnl:
        # Simplified Disposition Effect: uses realized PnL only.
        # Full Odean 1998 requires paper_gains/paper_losses (unrealized positions),
        # which needs position_id + entry/exit tracking not available in this schema.
        # This approximation uses win_rate as proxy for PGR and loss_rate as PLR.
        realized_gains = int((pnl > 0).sum())
        realized_losses = int((pnl < 0).sum())
        total = realized_gains + realized_losses
        # Proxy: PGR ≈ P(realize | gain), PLR ≈ P(realize | loss)
        # Without paper positions, we estimate from holding period if available
        pgr = realized_gains / max(total, 1)
        plr = realized_losses / max(total, 1)
        de_score = pgr - plr

        ci_low, ci_high = np.nan, np.nan
        if bootstrap_ci and total > 10:
            boot = oskill.bootstrap_distribution(
                pnl[pnl != 0], statistic=lambda x: (x > 0).mean() - (x < 0).mean(),
                n_bootstrap=n_bootstrap, random_state=random_state,
            )
            ci_low, ci_high = boot["ci_low"], boot["ci_high"]

        results["disposition"] = {
            "pgr": float(pgr), "plr": float(plr), "de_score": float(de_score),
            "ci_low": float(ci_low), "ci_high": float(ci_high),
            "interpretation": "strong" if de_score > 0.1 else "moderate" if de_score > 0 else "none",
            "n_trades_used": int(total),
            "note": "Simplified: full Odean 1998 requires paper_gains/paper_losses columns",
        }

    # Overtrading
    if "overtrading" in diagnostics:
        daily_counts = trades.groupby(trades["timestamp"].dt.date).size()
        if len(daily_counts) > 20:
            zscores = oprim.zscore_normalize(pd.Series(daily_counts.values.astype(float)),
                                             window=None, min_periods=1)
            latest_z = float(zscores.iloc[-1]) if not np.isnan(zscores.iloc[-1]) else 0.0
        else:
            latest_z = 0.0
        turnover = float(daily_counts.mean())
        results["overtrading"] = {
            "turnover_ratio": turnover,
            "zscore_vs_history": latest_z,
            "interpretation": "high" if abs(latest_z) > 2 else "normal",
            "n_periods": len(daily_counts),
        }

    # Chasing Momentum
    if "chasing" in diagnostics and benchmark_returns is not None:
        trade_dates = pd.to_datetime(trades["timestamp"]).dt.normalize()
        directions = trades["side"].map({"buy": 1, "sell": -1}).fillna(0).values
        momentum = benchmark_returns.rolling(lookback_momentum).mean()
        common = trade_dates.isin(momentum.index)
        if common.sum() > 10:
            mom_vals = momentum.reindex(trade_dates[common]).values
            dir_vals = directions[common.values]
            valid = ~np.isnan(mom_vals)
            corr = float(np.corrcoef(mom_vals[valid], dir_vals[valid])[0, 1])
        else:
            corr = np.nan
        results["chasing"] = {
            "momentum_correlation": corr,
            "ci_low": np.nan, "ci_high": np.nan,
            "interpretation": "chasing" if corr > 0.3 else "contrarian" if corr < -0.3 else "neutral",
        }

    # Anchoring
    if "anchoring" in diagnostics and has_pnl:
        # Check if exits cluster near entry prices (small pnl relative to price)
        pnl_pct = np.abs(pnl) / (trades["price"].values * trades["quantity"].values + 1e-10)
        concentration = float((pnl_pct < 0.05).mean())
        results["anchoring"] = {
            "exit_price_concentration": concentration,
            "anchor_zones_identified": int((pnl_pct < 0.02).sum()),
            "interpretation": "strong" if concentration > 0.6 else "moderate" if concentration > 0.4 else "weak",
        }

    # Outlier trades detection using oskill
    if has_pnl:
        outlier_result = oskill.detect_outliers_robust(pnl, methods=["zscore", "iqr"])
        n_outlier_trades = int(outlier_result["n_outliers"])
    else:
        n_outlier_trades = 0

    return {
        "diagnostics": results,
        "behavior_metrics": {
            "n_trades_total": n_trades,
            "win_rate": win_rate,
            "n_outlier_trades": n_outlier_trades,
        },
        "summary_report": {
            "primary_biases": [k for k, v in results.items()
                               if v.get("interpretation") in ("strong", "high", "chasing")],
            "n_trades_analyzed": n_trades,
            "warnings": [],
        },
    }


def shadow_account_simulator(
    actual_trades: pd.DataFrame,
    market_data: pd.DataFrame,
    rule_fn: Callable[[pd.Timestamp, dict], dict | None],
    *,
    initial_capital: float = 100000.0,
    regime_labels: pd.Series | None = None,
    bootstrap_significance: bool = True,
    n_bootstrap: int = 1000,
) -> dict:
    """Simulate shadow account following strict rules vs actual trades.

    Calls:
        oskill.regime_aware_performance, oskill.bootstrap_distribution,
        oprim.cumulative_returns, oprim.drawdown_curve

    Args:
        actual_trades: DataFrame with timestamp, symbol, side, quantity, price.
        market_data: DataFrame with timestamp index, columns = symbols, values = prices.
        rule_fn: Function(timestamp, context) → trade dict or None.
        initial_capital: Starting capital.
        regime_labels: Optional regime labels for breakdown.
        bootstrap_significance: Whether to test PnL difference significance.
        n_bootstrap: Bootstrap resamples.

    Returns:
        Dict with actual/shadow performance, comparison, equity curves.
    """
    if len(actual_trades) == 0:
        raise ValueError("actual_trades must not be empty")
    if len(market_data) == 0:
        raise ValueError("market_data must not be empty")

    dates = market_data.index
    actual_equity = [initial_capital]
    shadow_equity = [initial_capital]
    rule_violations = 0

    has_pnl = "pnl" in actual_trades.columns
    if not has_pnl:
        import warnings
        warnings.warn("actual_trades has no 'pnl' column; actual PnL defaults to 0", stacklevel=2)

    # Simple simulation: track daily PnL
    actual_trades_sorted = actual_trades.sort_values("timestamp")

    for i, date in enumerate(dates[1:], 1):
        # Actual: use trades PnL if available
        day_trades = actual_trades_sorted[
            pd.to_datetime(actual_trades_sorted["timestamp"]).dt.normalize() == pd.Timestamp(date).normalize()
        ]
        if has_pnl and len(day_trades) > 0:
            actual_pnl = day_trades["pnl"].sum()
        else:
            actual_pnl = 0.0

        # Shadow: apply rule_fn
        context = {"date": date, "equity": shadow_equity[-1], "market_data": market_data.iloc[:i]}
        shadow_decision = rule_fn(pd.Timestamp(date), context)
        shadow_pnl = 0.0
        if shadow_decision is not None:
            shadow_pnl = shadow_decision.get("pnl", 0.0)

        # Rule violation detection: compare direction/quantity, not just presence
        actual_side = day_trades["side"].values[0] if len(day_trades) > 0 else None
        actual_qty = day_trades["quantity"].sum() if len(day_trades) > 0 else 0
        shadow_side = shadow_decision.get("side") if shadow_decision else None
        shadow_qty = shadow_decision.get("quantity", 0) if shadow_decision else 0

        if shadow_decision is not None and len(day_trades) == 0:
            rule_violations += 1  # rule says trade, actual didn't
        elif shadow_decision is None and len(day_trades) > 0:
            rule_violations += 1  # rule says no trade, actual did
        elif shadow_decision is not None and len(day_trades) > 0:
            # Both traded: check direction/quantity mismatch
            if shadow_side and actual_side and shadow_side != actual_side:
                rule_violations += 1
            elif shadow_qty > 0 and actual_qty > 0 and abs(shadow_qty - actual_qty) / max(shadow_qty, actual_qty) > 0.2:
                rule_violations += 1

        actual_equity.append(actual_equity[-1] + actual_pnl)
        shadow_equity.append(shadow_equity[-1] + shadow_pnl)

    actual_eq = pd.Series(actual_equity, index=dates[:len(actual_equity)])
    shadow_eq = pd.Series(shadow_equity, index=dates[:len(shadow_equity)])

    # Compute returns
    actual_ret = actual_eq.pct_change().dropna()
    shadow_ret = shadow_eq.pct_change().dropna()
    actual_ret = actual_ret.replace([np.inf, -np.inf], 0).fillna(0)
    shadow_ret = shadow_ret.replace([np.inf, -np.inf], 0).fillna(0)

    # Performance using oprim
    actual_dd = oprim.drawdown_curve(actual_ret, input_type="returns")
    shadow_dd = oprim.drawdown_curve(shadow_ret, input_type="returns")
    actual_cum = oprim.cumulative_returns(actual_ret)
    shadow_cum = oprim.cumulative_returns(shadow_ret)

    actual_sharpe = oprim.sharpe_ratio(actual_ret) if len(actual_ret) > 30 else np.nan
    shadow_sharpe = oprim.sharpe_ratio(shadow_ret) if len(shadow_ret) > 30 else np.nan

    # PnL difference
    pnl_diff = float(actual_eq.iloc[-1] - shadow_eq.iloc[-1])
    pnl_diff_ci = (np.nan, np.nan)
    if bootstrap_significance and len(actual_ret) > 30:
        diff_returns = (actual_ret - shadow_ret).values
        boot = oskill.bootstrap_distribution(
            diff_returns, statistic=np.mean, n_bootstrap=n_bootstrap
        )
        pnl_diff_ci = (boot["ci_low"], boot["ci_high"])

    # Regime breakdown
    regime_breakdown = None
    if regime_labels is not None and len(actual_ret) > 0:
        common_idx = actual_ret.index.intersection(regime_labels.index)
        if len(common_idx) > 30:
            regime_breakdown = oskill.regime_aware_performance(
                actual_ret.loc[common_idx], regime_labels.loc[common_idx]
            )

    return {
        "actual_performance": {
            "total_return": float(actual_cum.iloc[-1]) if len(actual_cum) > 0 else 0.0,
            "sharpe": float(actual_sharpe),
            "max_drawdown": float(actual_dd["max_drawdown"]),
            "n_trades": len(actual_trades),
        },
        "shadow_performance": {
            "total_return": float(shadow_cum.iloc[-1]) if len(shadow_cum) > 0 else 0.0,
            "sharpe": float(shadow_sharpe),
            "max_drawdown": float(shadow_dd["max_drawdown"]),
        },
        "comparison": {
            "pnl_difference": pnl_diff,
            "pnl_difference_ci": pnl_diff_ci,
            "rule_violations": rule_violations,
        },
        "regime_breakdown": regime_breakdown,
        "actual_equity_curve": actual_eq,
        "shadow_equity_curve": shadow_eq,
    }


# ── Sprint 0 additions (v1.3.0) ──────────────────────────────────────────────

STABILITY_NEW = "experimental"


def monthly_trade_review(
    trades: list[dict],
    period: tuple[int, int],
    llm_client: Callable,
    prompt_builder: Callable[[dict, dict], str],
    discipline_evaluator: Optional[Callable] = None,
) -> dict:
    """Generate a monthly trade review with statistics and LLM-narrated insights.

    Parameters
    ----------
    trades : list of closed trades in the period (dicts with pnl_field)
    period : (year, month) tuple, e.g. (2026, 5)
    llm_client : sync or async callable for LLM (called synchronously here)
    prompt_builder : (trade_stats, period_dict) -> prompt string
    discipline_evaluator : optional callable(trade) -> float (discipline score)

    Returns
    -------
    {
        "period": "YYYY-MM",
        "summary_statistics": dict,
        "behavior_diagnostics": dict | None,
        "discipline_summary": dict | None,
        "llm_narrative": str,
        "key_insights": list[str],
        "recommended_focus_areas": list[str]
    }

    Uses: oskill.performance.trade_pnl_statistics
    """
    import oskill

    year, month = period
    period_str = f"{year:04d}-{month:02d}"

    summary_statistics = oskill.trade_pnl_statistics(trades)

    behavior_diagnostics: dict | None = None

    discipline_summary: dict | None = None
    if discipline_evaluator is not None and trades:
        scores = []
        for t in trades:
            try:
                s = discipline_evaluator(t)
                scores.append(float(s))
            except Exception:
                pass
        if scores:
            discipline_summary = {
                "avg_discipline_score": sum(scores) / len(scores),
                "n_evaluated": len(scores),
                "min_score": min(scores),
                "max_score": max(scores),
            }

    period_dict = {"year": year, "month": month, "period_str": period_str}
    prompt = prompt_builder(summary_statistics, period_dict)

    try:
        llm_response = llm_client(prompt)
    except Exception as exc:
        llm_response = f"[LLM unavailable: {exc}]"

    llm_narrative = str(llm_response) if llm_response else ""

    key_insights: list[str] = []
    recommended_focus_areas: list[str] = []

    win_rate = summary_statistics.get("win_rate", 0.0)
    avg_pnl = summary_statistics.get("avg_pnl", 0.0)

    if win_rate < 0.4:
        recommended_focus_areas.append("Improve entry precision (win_rate below 40%)")
    if avg_pnl < 0:
        recommended_focus_areas.append("Review loss management (avg PnL negative)")
    if summary_statistics.get("n_trades", 0) == 0:
        key_insights.append("No trades in this period")

    return {
        "period": period_str,
        "summary_statistics": summary_statistics,
        "behavior_diagnostics": behavior_diagnostics,
        "discipline_summary": discipline_summary,
        "llm_narrative": llm_narrative,
        "key_insights": key_insights,
        "recommended_focus_areas": recommended_focus_areas,
    }


def training_task_recommend(
    user_behavior_summary: dict,
    journal_summary: dict,
    llm_client: Callable,
    prompt_builder: Callable,
    task_taxonomy: list[dict],
) -> dict:
    """Recommend training tasks based on user behavior weaknesses.

    Parameters
    ----------
    user_behavior_summary : from omodul.trade_journal_analyzer (behavior_metrics field)
    journal_summary : aggregated journal stats (e.g. win_rate, avg_pnl)
    llm_client : sync callable for LLM
    prompt_builder : builds prompt from summaries
    task_taxonomy : caller-provided list of available task dicts
                   (each: {"task_type": str, "description": str, "targets": list[str]})

    Returns
    -------
    {
        "recommended_tasks": [{"task_type": str, "rationale": str, "expected_outcome": str}, ...],
        "weakness_identified": list[str],
        "llm_reasoning": str
    }

    Uses: omodul.trade_journal_analyzer output
    """
    weakness_identified: list[str] = []

    win_rate = journal_summary.get("win_rate", 1.0)
    avg_pnl = journal_summary.get("avg_pnl", 0.0)
    disposition_score = user_behavior_summary.get("disposition_effect_ratio", 0.0)

    if win_rate < 0.45:
        weakness_identified.append("low_win_rate")
    if avg_pnl < 0:
        weakness_identified.append("negative_avg_pnl")
    if disposition_score > 0.6:
        weakness_identified.append("disposition_effect")

    recommended_tasks: list[dict] = []
    for task in task_taxonomy:
        targets = task.get("targets", [])
        matched = any(w in targets for w in weakness_identified)
        if matched:
            recommended_tasks.append({
                "task_type": task.get("task_type", ""),
                "rationale": f"Targets: {', '.join(targets)}",
                "expected_outcome": task.get("description", ""),
            })

    summary_dict = {
        "weaknesses": weakness_identified,
        "journal_summary": journal_summary,
        "user_behavior_summary": user_behavior_summary,
    }
    prompt = prompt_builder(summary_dict)

    try:
        llm_response = llm_client(prompt)
    except Exception as exc:
        llm_response = f"[LLM unavailable: {exc}]"

    return {
        "recommended_tasks": recommended_tasks,
        "weakness_identified": weakness_identified,
        "llm_reasoning": str(llm_response) if llm_response else "",
    }
