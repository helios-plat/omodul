"""Group 3: Strategy Validation & Evaluation modules."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

import oprim
import oskill


def strategy_backtest_report(
    strategy_returns: pd.Series,
    *,
    benchmark_returns: pd.Series | None = None,
    regime_labels: pd.Series | None = None,
    factor_returns: pd.DataFrame | None = None,
    cpcv_config: dict | None = None,
    wfo_config: dict | None = None,
    n_bootstrap: int = 1000,
    annualization_factor: float = 252.0,
    report_format: Literal["dict", "markdown"] = "dict",
) -> dict | str:
    """Complete strategy backtest report.

    Calls:
        oskill.cpcv_pipeline, oskill.walk_forward_optimization, oskill.psr_dsr,
        oskill.bootstrap_sharpe, oskill.regime_aware_performance,
        oskill.factor_attribution, oprim.cumulative_returns, oprim.drawdown_curve,
        oprim.value_at_risk
    """
    if not isinstance(strategy_returns, pd.Series):
        strategy_returns = pd.Series(strategy_returns)
    if len(strategy_returns) < 10:
        raise ValueError("strategy_returns must have at least 10 observations")

    ret = strategy_returns.dropna()
    n = len(ret)

    # Basic stats
    cum = oprim.cumulative_returns(ret)
    dd = oprim.drawdown_curve(ret, input_type="returns")
    var_result = oprim.value_at_risk(ret, confidence_level=0.95)

    ann_return = float((1 + cum.iloc[-1]) ** (annualization_factor / n) - 1) if n > 0 else 0.0
    ann_vol = float(ret.std() * np.sqrt(annualization_factor))

    summary = {
        "total_periods": n,
        "annualized_return": ann_return,
        "annualized_volatility": ann_vol,
        "annualized_sharpe": float(oprim.sharpe_ratio(ret, annualization_factor=int(annualization_factor))),
        "max_drawdown": float(dd["max_drawdown"]),
        "var_95": float(var_result["var"]),
        "best_day": float(ret.max()),
        "worst_day": float(ret.min()),
    }

    # Robust Sharpe
    robust_sharpe = oskill.bootstrap_sharpe(
        ret.values, n_bootstrap=n_bootstrap, annualization_factor=annualization_factor
    )

    # PSR/DSR
    psr_dsr_result = oskill.psr_dsr(ret.values, annualization_factor=annualization_factor)

    # Optional: CPCV
    cpcv_result = None
    if cpcv_config is not None:
        cpcv_result = oskill.cpcv_pipeline(n, **cpcv_config)

    # Optional: WFO
    wfo_result = None
    if wfo_config is not None:
        wfo_result = oskill.walk_forward_optimization(n, **wfo_config)

    # Optional: Regime breakdown
    regime_breakdown = None
    if regime_labels is not None:
        common = ret.index.intersection(regime_labels.index)
        if len(common) > 30:
            regime_breakdown = oskill.regime_aware_performance(
                ret.loc[common], regime_labels.loc[common],
                annualization_factor=annualization_factor,
            )

    # Optional: Factor attribution
    factor_attr = None
    if factor_returns is not None:
        n_fac = min(len(ret), len(factor_returns))
        if n_fac >= 30:
            # Reset index to ensure alignment (ret may have different index than factor_returns)
            fac_aligned = factor_returns.iloc[:n_fac].reset_index(drop=True)
            factor_attr = oskill.factor_attribution(
                ret.values[:n_fac],
                fac_aligned,
                bootstrap_ci_enabled=True, n_bootstrap=min(500, n_bootstrap),
            )

    warnings_list = []
    if n < 60:
        warnings_list.append(f"Short history ({n} periods): results may be unreliable")

    result = {
        "summary": summary,
        "robust_sharpe": robust_sharpe,
        "psr_dsr": psr_dsr_result,
        "cpcv": cpcv_result,
        "wfo": wfo_result,
        "regime_breakdown": regime_breakdown,
        "factor_attribution": factor_attr,
        "warnings": warnings_list,
    }

    if report_format == "markdown":
        lines = [f"# Strategy Backtest Report", f"",
                 f"**Periods**: {n} | **Ann. Return**: {ann_return:.2%} | **Sharpe**: {summary['annualized_sharpe']:.2f}",
                 f"**Max DD**: {dd['max_drawdown']:.2%} | **VaR 95%**: {var_result['var']:.4f}",
                 f"**PSR**: {psr_dsr_result['psr']:.3f}"]
        return "\n".join(lines)

    return result


def strategy_decay_monitor(
    live_returns: pd.Series,
    baseline_returns: pd.Series,
    *,
    rolling_window: int = 60,
    sharpe_threshold_dead: float = 0.0,
    consecutive_periods_dead: int = 30,
    annualization_factor: float = 252.0,
    mk_alpha: float = 0.05,
    shift_alpha: float = 0.05,
) -> dict:
    """Monitor strategy decay with 4-state machine.

    Calls:
        oskill.bootstrap_sharpe, oskill.distribution_shift_test,
        oprim.mann_kendall_trend, oprim.zscore_normalize
    """
    if not isinstance(live_returns, pd.Series):
        live_returns = pd.Series(live_returns)
    if not isinstance(baseline_returns, pd.Series):
        baseline_returns = pd.Series(baseline_returns)
    if len(live_returns) < rolling_window:
        raise ValueError(f"live_returns ({len(live_returns)}) must be >= rolling_window ({rolling_window})")

    # Rolling Sharpe
    rolling_sharpe_vals = []
    for i in range(rolling_window, len(live_returns) + 1):
        window = live_returns.iloc[i - rolling_window:i].values
        sr = oprim.sharpe_ratio(pd.Series(window), annualization_factor=int(annualization_factor))
        rolling_sharpe_vals.append(sr)

    rolling_sharpe = pd.Series(rolling_sharpe_vals, index=live_returns.index[rolling_window - 1:])

    # Mann-Kendall trend test on rolling Sharpe
    valid_sharpe = rolling_sharpe.dropna().values
    trend_test = oprim.mann_kendall_trend(valid_sharpe) if len(valid_sharpe) > 10 else {
        "trend": "no_trend", "p_value": 1.0, "tau": 0.0
    }
    trend_significant = trend_test["p_value"] < mk_alpha
    trend_direction = trend_test.get("trend", "no_trend")

    # Distribution shift test (live vs baseline)
    live_vals = live_returns.values[-min(len(live_returns), 60):]
    base_vals = baseline_returns.values[-min(len(baseline_returns), 60):]
    if len(live_vals) > 20 and len(base_vals) > 20:
        shift_result = oskill.distribution_shift_test(live_vals, base_vals, alpha=shift_alpha)
        shift_detected = shift_result["shift_detected"]
    else:
        shift_result = {}
        shift_detected = False

    # Consecutive below threshold
    below = rolling_sharpe < sharpe_threshold_dead
    consecutive = 0
    for v in reversed(below.values):
        if v:
            consecutive += 1
        else:
            break

    # 4-state machine
    below_now = bool(rolling_sharpe.iloc[-1] < sharpe_threshold_dead) if len(rolling_sharpe) > 0 else False
    trend_decreasing = trend_significant and trend_direction in ("decreasing", "down")

    if consecutive >= consecutive_periods_dead:
        state = "DEAD"
    elif trend_decreasing and shift_detected:
        state = "CRITICAL"
    elif trend_decreasing or shift_detected:
        state = "DEGRADING"
    else:
        state = "HEALTHY"

    # Decay score: 0=healthy, 1=dead
    decay_score = {"HEALTHY": 0.0, "DEGRADING": 0.33, "CRITICAL": 0.67, "DEAD": 1.0}[state]

    return {
        "decay_state": state,
        "decay_score": decay_score,
        "rolling_sharpe": rolling_sharpe,
        "trend_test": trend_test,
        "distribution_shift_test": shift_result,
        "consecutive_below_threshold": consecutive,
        "diagnostics": {
            "trend_significant": trend_significant,
            "trend_direction": trend_direction,
            "shift_detected": shift_detected,
            "below_threshold_now": below_now,
        },
        "alert_message": f"Strategy state: {state} (score={decay_score:.2f})",
    }


def factor_attribution_report(
    asset_returns: pd.Series,
    factor_sets: dict[str, pd.DataFrame],
    *,
    bootstrap_ci_enabled: bool = True,
    n_bootstrap: int = 1000,
    include_residual_analysis: bool = True,
    include_rolling_alpha: bool = True,
    rolling_window: int = 60,
    standard_errors: Literal["ols", "white", "newey_west"] = "newey_west",
) -> dict:
    """Multi-model factor attribution report.

    Calls:
        oskill.factor_attribution, oskill.distribution_shift_test,
        oprim.mann_kendall_trend, oprim.kolmogorov_smirnov_test
    """
    if not isinstance(asset_returns, pd.Series):
        asset_returns = pd.Series(asset_returns)
    if len(factor_sets) == 0:
        raise ValueError("factor_sets must not be empty")
    if len(asset_returns) < 30:
        raise ValueError("asset_returns must have at least 30 observations")

    models = {}
    for model_name, factors_df in factor_sets.items():
        # Align lengths
        n = min(len(asset_returns), len(factors_df))
        ret = asset_returns.iloc[:n].values
        fac = factors_df.iloc[:n]

        # Factor attribution
        attr = oskill.factor_attribution(
            ret, fac, bootstrap_ci_enabled=bootstrap_ci_enabled,
            n_bootstrap=n_bootstrap, standard_errors=standard_errors,
        )

        model_result = {"attribution": attr}

        # Residual analysis
        if include_residual_analysis:
            predicted = attr["alpha"] + sum(
                attr["betas"][f] * fac[f].values for f in attr["factor_names"]
            )
            residuals = ret - predicted
            ks_test = oprim.kolmogorov_smirnov_test(residuals, "norm", mode="one_sample")
            model_result["residual_analysis"] = {
                "ks_statistic": ks_test["statistic"],
                "ks_pvalue": ks_test["p_value"],
                "residual_std": float(np.std(residuals)),
                "normality_rejected": ks_test["p_value"] < 0.05,
            }

        # Rolling alpha
        if include_rolling_alpha and n > rolling_window:
            rolling_alphas = []
            for i in range(rolling_window, n):
                window_ret = ret[i - rolling_window:i]
                window_fac = fac.iloc[i - rolling_window:i]
                try:
                    r = oskill.factor_attribution(
                        window_ret, window_fac, bootstrap_ci_enabled=False
                    )
                    rolling_alphas.append(r["alpha"])
                except Exception:
                    rolling_alphas.append(np.nan)
            alpha_arr = np.array(rolling_alphas)
            valid_alphas = alpha_arr[~np.isnan(alpha_arr)]
            alpha_trend = oprim.mann_kendall_trend(valid_alphas) if len(valid_alphas) > 10 else None
            model_result["rolling_alpha"] = {
                "values": alpha_arr,
                "trend_test": alpha_trend,
            }

        models[model_name] = model_result

    # Model comparison
    best_r2 = max(models.items(), key=lambda x: x[1]["attribution"]["r_squared"])
    comparison = {
        "best_r_squared": {"model": best_r2[0], "value": best_r2[1]["attribution"]["r_squared"]},
        "most_parsimonious": min(models.items(),
                                  key=lambda x: len(x[1]["attribution"]["factor_names"]))[0],
    }

    return {
        "models": models,
        "model_comparison": comparison,
        "summary": {"n_models": len(models), "n_observations": len(asset_returns)},
        "warnings": [],
    }
