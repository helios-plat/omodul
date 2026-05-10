"""Group 5: Risk Management modules."""

from __future__ import annotations

import numpy as np
import pandas as pd

import oprim
import oskill


def scenario_stress_test(
    portfolio_returns: pd.Series | pd.DataFrame,
    historical_data: pd.DataFrame,
    *,
    scenarios: list[dict],
    bootstrap_ci: bool = True,
    n_bootstrap: int = 1000,
    var_confidence: float = 0.95,
) -> dict:
    """Complete scenario stress test (historical + custom + analogy).

    Calls:
        oskill.historical_analogy_search, oskill.bootstrap_distribution,
        oprim.value_at_risk, oprim.drawdown_curve, oprim.cumulative_returns
    """
    if not isinstance(portfolio_returns, pd.Series):
        if isinstance(portfolio_returns, pd.DataFrame):
            portfolio_returns = portfolio_returns.iloc[:, 0]
        else:
            portfolio_returns = pd.Series(portfolio_returns)
    if len(scenarios) == 0:
        raise ValueError("scenarios must not be empty")
    if len(portfolio_returns) < 10:
        raise ValueError("portfolio_returns must have at least 10 observations")

    per_scenario = []

    for scenario in scenarios:
        s_type = scenario.get("type", "historical")
        s_name = scenario.get("name", f"scenario_{len(per_scenario)}")

        if s_type == "historical":
            start = pd.Timestamp(scenario["start"])
            end = pd.Timestamp(scenario["end"])
            mask = (historical_data.index >= start) & (historical_data.index <= end)
            period_data = historical_data.loc[mask]
            if len(period_data) == 0:
                per_scenario.append({"name": s_name, "type": s_type, "error": "no data in range"})
                continue
            scenario_returns = period_data.select_dtypes(include=[np.number]).iloc[:, 0].pct_change().dropna()

        elif s_type == "custom":
            shock_pct = scenario.get("shock_pct", -0.10)
            duration = scenario.get("duration_days", 5)
            daily_shock = (1 + shock_pct) ** (1 / duration) - 1
            scenario_returns = pd.Series([daily_shock] * duration)

        elif s_type == "analogy":
            # Use historical_analogy_search to find similar periods
            current = scenario.get("current_panel")
            if current is None:
                per_scenario.append({"name": s_name, "type": s_type, "error": "no current_panel"})
                continue
            query = current.select_dtypes(include=[np.number]).iloc[:, 0].values
            # Use historical_data as database
            window = len(query)
            db = []
            for i in range(0, len(historical_data) - window, window // 2):
                chunk = historical_data.iloc[i:i + window].select_dtypes(include=[np.number]).iloc[:, 0].values
                if len(chunk) == window:
                    db.append(chunk)
            if db:
                matches = oskill.historical_analogy_search(query, db, top_k=scenario.get("top_k", 5))
                # Use worst match as stress scenario
                worst_idx = matches[-1]["historical_idx"] if matches else 0
                start_pos = worst_idx * (window // 2)
                scenario_returns = historical_data.iloc[start_pos:start_pos + window].select_dtypes(
                    include=[np.number]).iloc[:, 0].pct_change().dropna()
            else:
                scenario_returns = pd.Series([0.0])
        else:
            per_scenario.append({"name": s_name, "type": s_type, "error": f"unknown type: {s_type}"})
            continue

        # Compute performance metrics
        if len(scenario_returns) > 0:
            cum = oprim.cumulative_returns(scenario_returns)
            perf = {
                "cumulative_return": float(cum.iloc[-1]) if len(cum) > 0 else 0.0,
                "n_days": len(scenario_returns),
            }
            if len(scenario_returns) >= 10:
                dd = oprim.drawdown_curve(scenario_returns, input_type="returns")
                var = oprim.value_at_risk(scenario_returns, confidence_level=var_confidence)
                perf["max_drawdown"] = float(dd["max_drawdown"])
                perf["var"] = float(var["var"])
                perf["es"] = float(var["es"])
            else:
                perf["max_drawdown"] = float(min(scenario_returns.cumsum()))
                perf["var"] = float(np.percentile(scenario_returns, (1 - var_confidence) * 100))
                perf["es"] = perf["var"]

            ci = None
            if bootstrap_ci and len(scenario_returns) > 10:
                boot = oskill.bootstrap_distribution(
                    scenario_returns.values, np.mean, n_bootstrap=n_bootstrap
                )
                ci = {"ci_low": boot["ci_low"], "ci_high": boot["ci_high"]}

            per_scenario.append({"name": s_name, "type": s_type, "performance": perf, "ci": ci})
        else:
            per_scenario.append({"name": s_name, "type": s_type, "performance": None, "ci": None})

    # Comparison table
    rows = []
    for s in per_scenario:
        if s.get("performance"):
            rows.append({"scenario": s["name"], **s["performance"]})
    comparison = pd.DataFrame(rows) if rows else pd.DataFrame()

    worst = min(per_scenario, key=lambda x: x.get("performance", {}).get("cumulative_return", 0)
                if x.get("performance") else 0)

    return {
        "per_scenario": per_scenario,
        "comparison": comparison,
        "worst_case_scenario": worst.get("name", ""),
        "summary": {"n_scenarios": len(scenarios), "n_computed": len(rows)},
    }


def tail_risk_analyzer(
    returns: pd.Series,
    *,
    confidence_levels: list[float] | None = None,
    methods: list[str] | None = None,
    bootstrap_ci: bool = True,
    n_bootstrap: int = 1000,
    include_normality_test: bool = True,
) -> dict:
    """Multi-method tail risk analysis.

    Calls:
        oprim.value_at_risk, oprim.skew_kurt_robust, oprim.kolmogorov_smirnov_test,
        oskill.bootstrap_distribution
    """
    if confidence_levels is None:
        confidence_levels = [0.95, 0.99]
    if methods is None:
        methods = ["historical", "parametric", "cornish_fisher"]

    if not isinstance(returns, pd.Series):
        returns = pd.Series(returns)
    if len(returns) < 20:
        raise ValueError("returns must have at least 20 observations")

    ret = returns.dropna()

    # VaR/ES table
    rows = []
    for method in methods:
        for cl in confidence_levels:
            var_result = oprim.value_at_risk(ret, confidence_level=cl, method=method)
            rows.append({
                "method": method, "confidence_level": cl,
                "var": float(var_result["var"]), "es": float(var_result["es"]),
            })
    var_es_table = pd.DataFrame(rows)

    # Tail metrics
    sk = oprim.skew_kurt_robust(ret.values)
    tail_metrics = {
        "skewness": sk["skewness"],
        "excess_kurtosis": sk["kurtosis_excess"],
        "max_loss": float(ret.min()),
        "n_extreme_observations": int((ret < ret.mean() - 3 * ret.std()).sum()),
    }

    # Normality test
    normality_test = None
    if include_normality_test:
        ks = oprim.kolmogorov_smirnov_test(ret.values, "norm", mode="one_sample")
        normality_test = {
            "ks_statistic": ks["statistic"],
            "ks_pvalue": ks["p_value"],
            "normality_rejected": ks["p_value"] < 0.05,
        }

    # Method comparison
    vars_95 = {r["method"]: r["var"] for r in rows if r["confidence_level"] == confidence_levels[0]}
    most_conservative = max(vars_95, key=vars_95.get) if vars_95 else None
    most_liberal = min(vars_95, key=vars_95.get) if vars_95 else None

    # Bootstrap CI for VaR
    ci_per_var = None
    if bootstrap_ci:
        ci_per_var = {}
        for cl in confidence_levels:
            boot = oskill.bootstrap_distribution(
                ret.values, statistic=lambda x, q=cl: float(np.percentile(x, (1 - q) * 100)),
                n_bootstrap=n_bootstrap,
            )
            ci_per_var[f"var_{int(cl*100)}"] = {"ci_low": boot["ci_low"], "ci_high": boot["ci_high"]}

    return {
        "var_es_table": var_es_table,
        "tail_metrics": tail_metrics,
        "normality_test": normality_test,
        "method_comparison": {
            "most_conservative": most_conservative,
            "most_liberal": most_liberal,
        },
        "ci_per_var_estimate": ci_per_var,
        "summary": {"n_observations": len(ret), "methods": methods, "confidence_levels": confidence_levels},
    }
