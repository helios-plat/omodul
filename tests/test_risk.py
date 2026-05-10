"""Tests for Group 5: Risk modules."""

import numpy as np
import pandas as pd
import pytest

from omodul.risk import scenario_stress_test, tail_risk_analyzer


class TestScenarioStressTest:
    def test_historical_scenario(self, spy_returns, btc_panel):
        scenarios = [{"name": "test", "type": "historical",
                      "start": "2023-03-01", "end": "2023-06-01"}]
        result = scenario_stress_test(spy_returns, btc_panel, scenarios=scenarios, n_bootstrap=100)
        assert "per_scenario" in result
        assert result["summary"]["n_scenarios"] == 1

    def test_custom_scenario(self, spy_returns, btc_panel):
        scenarios = [{"name": "crash", "type": "custom", "shock_pct": -0.20, "duration_days": 5}]
        result = scenario_stress_test(spy_returns, btc_panel, scenarios=scenarios)
        # Custom shock of -20% over 5 days should produce negative cumulative return
        perf = result["per_scenario"][0]["performance"]
        assert perf["cumulative_return"] < 0 or perf["max_drawdown"] < 0

    def test_multiple_scenarios(self, spy_returns, btc_panel):
        scenarios = [
            {"name": "mild", "type": "custom", "shock_pct": -0.05, "duration_days": 3},
            {"name": "severe", "type": "custom", "shock_pct": -0.30, "duration_days": 10},
        ]
        result = scenario_stress_test(spy_returns, btc_panel, scenarios=scenarios)
        assert len(result["per_scenario"]) == 2
        assert result["worst_case_scenario"] == "severe"

    def test_empty_scenarios_raises(self):
        with pytest.raises(ValueError, match="empty"):
            scenario_stress_test(pd.Series([0.01] * 20), pd.DataFrame({"x": range(20)}), scenarios=[])

    def test_short_returns_raises(self):
        with pytest.raises(ValueError, match="at least 10"):
            scenario_stress_test(pd.Series([0.01] * 5), pd.DataFrame(), scenarios=[{"type": "custom"}])


class TestTailRiskAnalyzer:
    def test_basic_analysis(self, spy_returns):
        result = tail_risk_analyzer(spy_returns, n_bootstrap=100)
        assert "var_es_table" in result
        assert "tail_metrics" in result
        assert len(result["var_es_table"]) == 6  # 3 methods × 2 confidence levels

    def test_normality_test(self, spy_returns):
        result = tail_risk_analyzer(spy_returns, include_normality_test=True)
        assert result["normality_test"] is not None
        assert "ks_pvalue" in result["normality_test"]

    def test_method_comparison(self, spy_returns):
        result = tail_risk_analyzer(spy_returns)
        assert result["method_comparison"]["most_conservative"] is not None

    def test_bootstrap_ci(self, spy_returns):
        result = tail_risk_analyzer(spy_returns, bootstrap_ci=True, n_bootstrap=100)
        assert result["ci_per_var_estimate"] is not None

    def test_short_returns_raises(self):
        with pytest.raises(ValueError, match="at least 20"):
            tail_risk_analyzer(pd.Series([0.01] * 10))

    def test_real_data_dogfood(self, spy_returns):
        """End-to-end with real SPY data."""
        result = tail_risk_analyzer(spy_returns, n_bootstrap=200)
        assert result["tail_metrics"]["skewness"] != 0
        assert result["var_es_table"]["var"].min() > 0

    def test_custom_scenario_first_day_shock(self, spy_returns, btc_panel):
        """Test shock_distribution='first_day'."""
        scenarios = [{"name": "crash", "type": "custom", "shock_pct": -0.15,
                      "duration_days": 5, "shock_distribution": "first_day"}]
        result = scenario_stress_test(spy_returns, btc_panel, scenarios=scenarios)
        assert result["per_scenario"][0]["performance"] is not None

    def test_custom_scenario_linear_shock(self, spy_returns, btc_panel):
        """Test shock_distribution='linear'."""
        scenarios = [{"name": "slow", "type": "custom", "shock_pct": -0.10,
                      "duration_days": 10, "shock_distribution": "linear"}]
        result = scenario_stress_test(spy_returns, btc_panel, scenarios=scenarios)
        assert result["per_scenario"][0]["performance"]["n_days"] == 10

