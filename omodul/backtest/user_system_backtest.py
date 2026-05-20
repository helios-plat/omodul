"""Backtest a user-defined trading system over historical periods."""

from __future__ import annotations

from datetime import date
from typing import Callable

import oprim
import oskill

STABILITY = "experimental"


def user_system_backtest(
    system_config: dict,
    ohlcv_history: dict[str, list[dict]],
    regime_history: list[dict],
    signal_detectors: list[Callable],
    market_rules: dict,
    period_years: int = 5,
) -> dict:
    """Run a multi-year backtest of a user-defined trading system.

    Parameters
    ----------
    system_config : user's system parameters:
                   {"initial_capital": float, "position_size_by_regime": {regime: float},
                    "max_positions": int, "entry_rules": dict, "exit_rules": dict}
    ohlcv_history : {symbol: [{"date": date, "open": float, ...}]}
    regime_history : [{"date": date, "regime": str, "score": float}]
    signal_detectors : list of callables; each receives (symbol, ohlcv) -> list[dict]
                       where each dict is a signal event {"date": date, "side": str, ...}
    market_rules : same format as paper_trading_session
    period_years : backtest period in years

    Returns
    -------
    {
        "config_used": dict,
        "trades": list[dict],
        "equity_curve": list[tuple[date, float]],
        "metrics": dict,
        "regime_breakdown": dict,
        "sensitivity_analysis": dict
    }

    Methodology
    -----------
    1. Generate signals using signal_detectors on history
    2. Apply system_config rules (position sizing per regime, etc.)
    3. Run backtest via oskill.backtest.market_rules_backtest_run
    4. Compute regime-conditional metrics
    5. Sensitivity analysis on key parameters

    Uses: oskill.backtest.market_rules_backtest_run, oskill.performance.portfolio_metrics_summary
    """
    from oskill.backtest.market_rules_backtest import market_rules_backtest_run

    initial_capital = float(system_config.get("initial_capital", 1_000_000))
    position_size_by_regime = system_config.get("position_size_by_regime", {})
    default_size = float(system_config.get("default_size_fraction", 0.1))

    regime_lookup: dict[date, str] = {
        r.get("date"): r.get("regime", "unknown")
        for r in regime_history
        if r.get("date") is not None
    }

    all_signals: list[dict] = []
    for symbol, bars in ohlcv_history.items():
        for detector in signal_detectors:
            try:
                detected = detector(symbol, bars)
                if detected:
                    for sig in detected:
                        sig_date = sig.get("date", date.min)
                        regime = regime_lookup.get(sig_date, "unknown")
                        size_fraction = position_size_by_regime.get(regime, default_size)
                        all_signals.append({
                            **sig,
                            "symbol": symbol,
                            "size_fraction": size_fraction,
                        })
            except Exception:
                pass

    backtest_result = market_rules_backtest_run(
        all_signals, ohlcv_history, market_rules, initial_capital=initial_capital
    )

    trades = backtest_result["trades"]
    equity_curve = backtest_result["equity_curve"]
    metrics = backtest_result["metrics"]

    regime_breakdown: dict[str, dict] = {}
    for trade in trades:
        trade_date = trade.get("exit_date")
        regime = regime_lookup.get(trade_date, "unknown") if trade_date else "unknown"
        if regime not in regime_breakdown:
            regime_breakdown[regime] = {"trades": [], "total_pnl": 0.0, "n_trades": 0}
        regime_breakdown[regime]["trades"].append(trade)
        regime_breakdown[regime]["total_pnl"] += trade.get("pnl", 0.0)
        regime_breakdown[regime]["n_trades"] += 1

    for grp in regime_breakdown:
        grp_trades = regime_breakdown[grp]["trades"]
        wins = sum(1 for t in grp_trades if t.get("pnl", 0) > 0)
        n = len(grp_trades)
        regime_breakdown[grp]["win_rate"] = wins / n if n > 0 else 0.0
        del regime_breakdown[grp]["trades"]

    sensitivity_analysis: dict = {
        "default_size": {
            "param": "default_size_fraction",
            "current": default_size,
            "note": "Sensitivity not computed (requires multiple runs)",
        }
    }

    return {
        "config_used": system_config,
        "trades": trades,
        "equity_curve": equity_curve,
        "metrics": metrics,
        "regime_breakdown": regime_breakdown,
        "sensitivity_analysis": sensitivity_analysis,
    }
