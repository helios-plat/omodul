"""Group 1: Trading Behavior Analysis modules."""

from __future__ import annotations

from typing import Callable, Literal

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

    # Disposition Effect
    if "disposition" in diagnostics and has_pnl:
        realized_gains = (pnl > 0).sum()
        realized_losses = (pnl < 0).sum()
        total = realized_gains + realized_losses
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

    # Simple simulation: track daily PnL
    actual_trades_sorted = actual_trades.sort_values("timestamp")

    for i, date in enumerate(dates[1:], 1):
        # Actual: use trades PnL if available
        day_trades = actual_trades_sorted[
            pd.to_datetime(actual_trades_sorted["timestamp"]).dt.normalize() == pd.Timestamp(date).normalize()
        ]
        if "pnl" in day_trades.columns and len(day_trades) > 0:
            actual_pnl = day_trades["pnl"].sum()
        else:
            actual_pnl = 0.0

        # Shadow: apply rule_fn
        context = {"date": date, "equity": shadow_equity[-1], "market_data": market_data.iloc[:i]}
        shadow_decision = rule_fn(pd.Timestamp(date), context)
        shadow_pnl = 0.0
        if shadow_decision is not None:
            shadow_pnl = shadow_decision.get("pnl", 0.0)

        if shadow_decision is not None and len(day_trades) == 0:
            rule_violations += 1
        elif shadow_decision is None and len(day_trades) > 0:
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
