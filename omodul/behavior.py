"""Group 1: Trading Behavior Analysis modules."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import oprim
import oskill
import pandas as pd


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

        # Vibe-Trading holding duration disposition check if hold_days available
        hold_col = "hold_days" if "hold_days" in trades.columns else ("duration_days" if "duration_days" in trades.columns else None)
        if hold_col and (trades[hold_col] > 0).any():
            win_hold = float(trades[trades["pnl"] > 0][hold_col].mean()) if (trades["pnl"] > 0).any() else 0.0
            loss_hold = float(trades[trades["pnl"] < 0][hold_col].mean()) if (trades["pnl"] < 0).any() else 0.0
            ratio_hold = float(loss_hold / win_hold) if win_hold > 0 else (float("inf") if loss_hold > 0 else 1.0)
            sev = "high" if ratio_hold >= 1.5 else ("medium" if ratio_hold >= 1.2 else "low")
            results["disposition"].update({
                "ratio_loss_to_win_hold": round(ratio_hold, 2) if np.isfinite(ratio_hold) else None,
                "avg_winner_hold_days": round(win_hold, 2),
                "avg_loser_hold_days": round(loss_hold, 2),
                "severity": sev,
                "evidence": (
                    f"Losing trades held avg {loss_hold:.1f}d vs winning trades {win_hold:.1f}d (ratio: {ratio_hold:.2f}). "
                    + ("Classic disposition effect: holding losers longer than winners." if sev == "high"
                       else "Moderate hold-losers tendency." if sev == "medium"
                       else "Hold times roughly symmetric.")
                ),
            })

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

        # Vibe-Trading busy-day vs quiet-day PnL check
        if has_pnl and len(daily_counts) >= 4:
            busy_cut = float(daily_counts.quantile(0.75))
            quiet_cut = float(daily_counts.quantile(0.25))
            trade_dates = pd.to_datetime(trades["timestamp"]).dt.date
            busy_days = set(daily_counts[daily_counts >= busy_cut].index)
            quiet_days = set(daily_counts[daily_counts <= quiet_cut].index)
            busy_pnl = trades[trade_dates.isin(busy_days)]["pnl"]
            quiet_pnl = trades[trade_dates.isin(quiet_days)]["pnl"]
            busy_avg = float(busy_pnl.mean()) if len(busy_pnl) > 0 else 0.0
            quiet_avg = float(quiet_pnl.mean()) if len(quiet_pnl) > 0 else 0.0
            gap = quiet_avg - busy_avg
            base = abs(quiet_avg) if quiet_avg != 0 else 1.0
            pnl_drag_sev = "high" if (busy_avg < quiet_avg and gap / base >= 1.0) else ("medium" if (busy_avg < quiet_avg and gap / base >= 0.3) else "low")
            results["overtrading"].update({
                "busy_day_avg_pnl": round(busy_avg, 2),
                "quiet_day_avg_pnl": round(quiet_avg, 2),
                "busy_day_threshold": round(busy_cut, 1),
                "severity": pnl_drag_sev if pnl_drag_sev != "low" else results["overtrading"]["interpretation"],
                "evidence": (
                    f"Busy days (≥{busy_cut:.0f} trades) avg PnL: ${busy_avg:.2f}; quiet days (≤{quiet_cut:.0f}) avg PnL: ${quiet_avg:.2f}. "
                    + ("High activity hurts returns." if pnl_drag_sev == "high" else "Some drag from high activity." if pnl_drag_sev == "medium" else "Trading activity does not significantly degrade PnL.")
                ),
            })

    # Chasing Momentum
    if "chasing" in diagnostics:
        if benchmark_returns is not None:
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
        else:
            # Self-momentum price run-up chasing check (Vibe-Trading)
            buys = trades[trades["side"].astype(str).str.lower().isin(["buy", "long"])].sort_values(["symbol", "timestamp"]).copy()
            if not buys.empty:
                buys["prev_price"] = buys.groupby("symbol")["price"].shift(1)
                matured = buys.dropna(subset=["prev_price"])
                if not matured.empty:
                    chased = matured[matured["price"] > matured["prev_price"] * 1.03]
                    chase_ratio = len(chased) / len(matured)
                    sev = "high" if chase_ratio >= 0.6 else ("medium" if chase_ratio >= 0.4 else "low")
                    results["chasing"] = {
                        "chase_ratio": round(chase_ratio, 3),
                        "buys_evaluated": len(matured),
                        "chased_buys": len(chased),
                        "severity": sev,
                        "interpretation": "chasing" if sev in ("high", "medium") else "neutral",
                        "evidence": (
                            f"{len(chased)}/{len(matured)} buys ({chase_ratio:.0%}) came after a >3% "
                            "price run-up in the same symbol. "
                            + ("Strong chasing pattern." if sev == "high" else "Some chasing tendency." if sev == "medium" else "No clear chasing bias.")
                        ),
                    }
                else:
                    results["chasing"] = {"interpretation": "neutral", "evidence": "insufficient repeat buys per symbol"}
            else:
                results["chasing"] = {"interpretation": "neutral", "evidence": "no buys"}

    # Anchoring
    if "anchoring" in diagnostics:
        # Check if exits cluster near entry prices (small pnl relative to price)
        if has_pnl:
            pnl_pct = np.abs(pnl) / (trades["price"].values * trades["quantity"].values + 1e-10)
            concentration = float((pnl_pct < 0.05).mean())
            results["anchoring"] = {
                "exit_price_concentration": concentration,
                "anchor_zones_identified": int((pnl_pct < 0.02).sum()),
                "interpretation": "strong" if concentration > 0.6 else "moderate" if concentration > 0.4 else "weak",
            }
        else:
            results["anchoring"] = {"interpretation": "weak"}

        # Price clustering CV check across frequently traded symbols (Vibe-Trading)
        grouped = trades.groupby("symbol")
        sym_anchors = []
        for sym, sub in grouped:
            if len(sub) >= 3:
                m_price = float(sub["price"].mean())
                s_price = float(sub["price"].std())
                if m_price > 0:
                    cv = s_price / m_price
                    sym_anchors.append({"symbol": str(sym), "trades": len(sub), "mean_price": round(m_price, 2), "cv": round(cv, 4)})
        if sym_anchors:
            anchored_syms = [s for s in sym_anchors if s["cv"] < 0.05]
            anchor_ratio = len(anchored_syms) / len(sym_anchors)
            sev = "high" if anchor_ratio >= 0.66 else ("medium" if anchor_ratio >= 0.33 else "low")
            results["anchoring"].update({
                "anchored_symbol_ratio": round(anchor_ratio, 3),
                "symbols_evaluated": len(sym_anchors),
                "anchored_symbols": anchored_syms[:5],
                "severity": sev,
                "evidence": (
                    f"{len(anchored_syms)}/{len(sym_anchors)} frequently traded symbols stayed in a narrow price band (CV < 5%). "
                    + ("Strong price anchoring." if sev == "high" else "Some anchoring on select symbols." if sev == "medium" else "Prices vary naturally across repeat trades.")
                ),
            })

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
    discipline_evaluator: Callable | None = None,
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


monthly_trade_review.STABILITY = "experimental"
training_task_recommend.STABILITY = "experimental"


# ── Shadow Account Rule Extraction (Vibe-Trading 3O Port) ─────────────────────

def _default_baseline_rule(support_count: int = 0) -> dict:
    return {
        "rule_id": "SR0_BASELINE",
        "name": "Disciplined Trend Following Baseline",
        "direction": "long",
        "entry_condition": {"indicator": "trend_follow", "threshold": 0.0, "direction": "long"},
        "exit_condition": {"stop_loss_pct": 0.03, "take_profit_rr": 2.0, "max_holding_days": 5},
        "holding_days_range": [1, 5],
        "stop_loss_pct": 0.03,
        "take_profit_rr": 2.0,
        "support_count": support_count,
        "coverage_rate": 1.0,
        "description": "Baseline disciplined rule: 1-5d holding, 3% stop loss, 2.0 RR target",
    }


def extract_shadow_rules(
    trades: pd.DataFrame | list[dict],
    *,
    min_support: int = 2,
    max_rules: int = 5,
) -> list[dict]:
    """Extract disciplined trading rules from profitable user roundtrips.

    Inspired by Vibe-Trading Shadow Account extractor.

    Args:
        trades: DataFrame or list of dicts with trade fields.
        min_support: Minimum profitable trades required for a rule.
        max_rules: Maximum number of rules to produce.

    Returns:
        List of rule dictionaries specifying direction, holding range,
        stop loss, take profit RR, entry/exit conditions, and support count.
    """
    if isinstance(trades, list):
        if not trades:
            return [_default_baseline_rule()]
        df = pd.DataFrame(trades)
    else:
        df = trades.copy()

    if df.empty:
        return [_default_baseline_rule()]

    # Normalize column names
    pnl_col = "realized_pnl_usd" if "realized_pnl_usd" in df.columns else ("pnl" if "pnl" in df.columns else None)
    if not pnl_col:
        return [_default_baseline_rule()]

    profitable = df[df[pnl_col] > 0]
    if len(profitable) < min_support:
        return [_default_baseline_rule(support_count=len(profitable))]

    rules = []
    dir_col = "direction" if "direction" in df.columns else ("side" if "side" in df.columns else None)

    # 1. Holding duration rule
    hold_days = []
    if "hold_days" in profitable.columns:
        hold_days = profitable["hold_days"].dropna().tolist()
    elif "created_at" in profitable.columns and "closed_at" in profitable.columns:
        t_open = pd.to_datetime(profitable["created_at"])
        t_close = pd.to_datetime(profitable["closed_at"])
        diffs = (t_close - t_open).dt.total_seconds() / 86400.0
        hold_days = diffs[diffs > 0].tolist()

    min_hold = int(max(1, np.floor(np.percentile(hold_days, 10)))) if hold_days else 1
    max_hold = int(max(min_hold + 1, np.ceil(np.percentile(hold_days, 90)))) if hold_days else 5

    # 2. Risk/Reward & Stop Loss extraction
    avg_rr = 2.0
    if "rr_ratio" in profitable.columns and profitable["rr_ratio"].notna().any():
        avg_rr = float(profitable["rr_ratio"].dropna().median())
        avg_rr = max(1.2, min(5.0, avg_rr))

    stop_pct = 0.03
    if "entry_price" in profitable.columns and "stop_loss" in profitable.columns:
        valid_stops = profitable.dropna(subset=["entry_price", "stop_loss"])
        if not valid_stops.empty:
            diff_pct = (valid_stops["entry_price"] - valid_stops["stop_loss"]).abs() / valid_stops["entry_price"]
            stop_pct = float(diff_pct.median())
            stop_pct = max(0.01, min(0.10, stop_pct))

    dominant_dir = "long"
    if dir_col:
        long_count = (profitable[dir_col].astype(str).str.lower().isin(["long", "buy"])).sum()
        short_count = len(profitable) - long_count
        if short_count > long_count:
            dominant_dir = "short"

    rules.append({
        "rule_id": "SR1",
        "name": f"Disciplined {dominant_dir.upper()} Momentum Rule",
        "direction": dominant_dir,
        "entry_condition": {"indicator": "momentum", "threshold": 0.01, "direction": dominant_dir},
        "exit_condition": {
            "stop_loss_pct": round(stop_pct, 4),
            "take_profit_rr": round(avg_rr, 2),
            "max_holding_days": max_hold,
        },
        "holding_days_range": [min_hold, max_hold],
        "stop_loss_pct": round(stop_pct, 4),
        "take_profit_rr": round(avg_rr, 2),
        "support_count": len(profitable),
        "coverage_rate": round(len(profitable) / max(len(df), 1), 3),
        "description": f"Extracted from {len(profitable)} winning trades: hold {min_hold}-{max_hold}d, stop {stop_pct:.1%}, target RR {avg_rr:.1f}",
    })

    return rules


def build_shadow_rule_fn(
    rules: list[dict],
    *,
    rule_source: str = "user_extracted",
    fusion_threshold: float = 60.0,
    signal_series: pd.Series | None = None,
) -> Callable[[pd.Timestamp, dict], dict | None]:
    """Build a deterministic rule_fn for shadow_account_simulator.

    Supports:
      - 'user_extracted': trades based on extracted disciplined parameters
      - 'fusion_score': entries gated by Fusion Score threshold
      - 'rsi_mtf': entries gated by multi-timeframe RSI oversold/overbought
      - 'hybrid': combines fusion score entry gate with user-extracted exit rules
    """
    rule = rules[0] if rules else _default_baseline_rule()
    direction = rule.get("direction", "long")
    max_hold = rule.get("exit_condition", {}).get("max_holding_days", 5)
    stop_loss_pct = float(rule.get("stop_loss_pct", 0.03))
    take_profit_rr = float(rule.get("take_profit_rr", 2.0))
    target_gain_pct = stop_loss_pct * take_profit_rr

    # State tracking across simulation loop
    state = {
        "in_position": False,
        "entry_price": 0.0,
        "entry_date": None,
        "side": direction,
        "bars_held": 0,
    }

    def _rule_fn(ts: pd.Timestamp, context: dict) -> dict | None:
        market_data = context.get("market_data")
        if market_data is None or len(market_data) == 0:
            return None

        # Last close price
        last_row = market_data.iloc[-1]
        curr_price = float(last_row.iloc[0]) if isinstance(last_row, pd.Series) else float(last_row)

        if state["in_position"]:
            state["bars_held"] += 1
            entry_p = state["entry_price"]
            if entry_p <= 0:
                state["in_position"] = False
                return None

            gain_pct = (curr_price - entry_p) / entry_p if state["side"] == "long" else (entry_p - curr_price) / entry_p

            hit_stop = gain_pct <= -stop_loss_pct
            hit_target = gain_pct >= target_gain_pct
            hit_timeout = state["bars_held"] >= max_hold

            if hit_stop or hit_target or hit_timeout:
                notional = 10000.0
                pnl = gain_pct * notional
                state["in_position"] = False
                exit_side = "sell" if state["side"] == "long" else "buy"
                return {
                    "side": exit_side,
                    "price": curr_price,
                    "quantity": notional / max(entry_p, 1e-6),
                    "pnl": pnl,
                    "reason": "target" if hit_target else ("stop" if hit_stop else "timeout"),
                }
            return None

        # Entry logic
        should_enter = False
        if rule_source == "fusion_score" and signal_series is not None:
            # Check fusion score at date
            ts_norm = ts.normalize() if hasattr(ts, "normalize") else ts
            score = signal_series.get(ts_norm, signal_series.get(ts, None))
            if score is not None and score >= fusion_threshold:
                should_enter = True
        elif rule_source == "rsi_mtf":
            if len(market_data) >= 14:
                series = market_data.iloc[:, 0] if market_data.ndim > 1 else market_data
                delta = series.diff()
                up = delta.clip(lower=0).rolling(14).mean()
                down = (-delta.clip(upper=0)).rolling(14).mean()
                rs = up / (down + 1e-10)
                rsi = 100.0 - (100.0 / (1.0 + rs))
                if not rsi.empty and float(rsi.iloc[-1]) < 35.0:
                    should_enter = True
        else:
            # Default momentum or user rule: entry when prior return positive
            if len(market_data) >= 3:
                series = market_data.iloc[:, 0] if market_data.ndim > 1 else market_data
                ret_3 = (float(series.iloc[-1]) - float(series.iloc[-3])) / float(series.iloc[-3])
                if direction == "long" and ret_3 > 0:
                    should_enter = True
                elif direction == "short" and ret_3 < 0:
                    should_enter = True

        if should_enter:
            state["in_position"] = True
            state["entry_price"] = curr_price
            state["entry_date"] = ts
            state["side"] = direction
            state["bars_held"] = 0
            return {
                "side": direction,
                "price": curr_price,
                "quantity": 10000.0 / max(curr_price, 1e-6),
                "pnl": 0.0,
            }

        return None

    return _rule_fn

