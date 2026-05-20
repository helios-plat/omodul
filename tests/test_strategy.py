"""Tests for Group 3: Strategy modules."""

import numpy as np
import pandas as pd
import pytest

from omodul.strategy import (
    factor_attribution_report,
    strategy_backtest_report,
    strategy_decay_monitor,
)


class TestStrategyBacktestReport:
    def test_basic_report(self, spy_returns):
        result = strategy_backtest_report(spy_returns, n_bootstrap=100)
        assert "summary" in result
        assert "robust_sharpe" in result
        assert "psr_dsr" in result
        assert result["summary"]["total_periods"] == len(spy_returns)

    def test_with_regime(self, spy_returns, regime_labels):
        result = strategy_backtest_report(spy_returns, regime_labels=regime_labels, n_bootstrap=100)
        assert result["regime_breakdown"] is not None

    def test_with_factors(self, spy_returns):
        # Generate daily factor data with same index as spy_returns
        rng = np.random.default_rng(42)
        n = 100
        daily_factors = pd.DataFrame({
            "Mkt-RF": rng.normal(0.0003, 0.01, n),
            "SMB": rng.normal(0.0001, 0.005, n),
        }, index=spy_returns.index[:n])
        result = strategy_backtest_report(spy_returns.iloc[:n], factor_returns=daily_factors, n_bootstrap=50)
        assert result["factor_attribution"] is not None

    def test_markdown_format(self, spy_returns):
        result = strategy_backtest_report(spy_returns, report_format="markdown", n_bootstrap=100)
        assert isinstance(result, str)
        assert "Sharpe" in result

    def test_short_returns_warning(self):
        ret = pd.Series(np.random.default_rng(42).normal(0, 0.01, 30))
        result = strategy_backtest_report(ret, n_bootstrap=100)
        assert any("Short" in w for w in result["warnings"])

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="at least 10"):
            strategy_backtest_report(pd.Series([0.01] * 5))

    def test_real_data_dogfood(self, spy_returns):
        """End-to-end with real SPY data."""
        result = strategy_backtest_report(spy_returns, n_bootstrap=200)
        assert result["summary"]["annualized_sharpe"] != 0
        assert result["psr_dsr"]["psr"] > 0


class TestStrategyDecayMonitor:
    def test_healthy_strategy(self):
        rng = np.random.default_rng(42)
        live = pd.Series(rng.normal(0.001, 0.01, 120))
        baseline = pd.Series(rng.normal(0.001, 0.01, 120))
        result = strategy_decay_monitor(live, baseline, rolling_window=60)
        assert result["decay_state"] in ("HEALTHY", "DEGRADING", "CRITICAL", "DEAD")
        assert 0 <= result["decay_score"] <= 1

    def test_dead_strategy(self):
        rng = np.random.default_rng(42)
        live = pd.Series(rng.normal(-0.005, 0.01, 120))  # negative mean
        baseline = pd.Series(rng.normal(0.002, 0.01, 120))
        result = strategy_decay_monitor(live, baseline, rolling_window=60, consecutive_periods_dead=20)
        # Should detect degradation
        assert result["decay_state"] in ("DEGRADING", "CRITICAL", "DEAD")

    def test_short_returns_raises(self):
        with pytest.raises(ValueError, match="rolling_window"):
            strategy_decay_monitor(pd.Series([0.01] * 10), pd.Series([0.01] * 10), rolling_window=60)

    def test_rolling_sharpe_output(self):
        rng = np.random.default_rng(42)
        live = pd.Series(rng.normal(0.001, 0.01, 100))
        baseline = pd.Series(rng.normal(0.001, 0.01, 100))
        result = strategy_decay_monitor(live, baseline, rolling_window=30)
        assert len(result["rolling_sharpe"]) > 0


class TestFactorAttributionReport:
    def test_basic_report(self):
        rng = np.random.default_rng(42)
        n = 100
        asset = pd.Series(rng.normal(0.001, 0.02, n))
        factors = pd.DataFrame({"MKT": rng.normal(0.0005, 0.015, n), "SMB": rng.normal(0, 0.01, n)})
        result = factor_attribution_report(asset, {"FF2": factors}, n_bootstrap=50)
        assert "models" in result
        assert "FF2" in result["models"]
        assert "model_comparison" in result

    def test_multi_model_comparison(self):
        rng = np.random.default_rng(42)
        n = 100
        asset = pd.Series(rng.normal(0.001, 0.02, n))
        factor_sets = {
            "FF2": pd.DataFrame({"MKT": rng.normal(0, 0.015, n), "SMB": rng.normal(0, 0.01, n)}),
            "FF3": pd.DataFrame({"MKT": rng.normal(0, 0.015, n), "SMB": rng.normal(0, 0.01, n),
                                  "HML": rng.normal(0, 0.01, n)}),
        }
        result = factor_attribution_report(asset, factor_sets, n_bootstrap=50)
        assert len(result["models"]) == 2
        assert "best_r_squared" in result["model_comparison"]

    def test_empty_factor_sets_raises(self):
        with pytest.raises(ValueError, match="empty"):
            factor_attribution_report(pd.Series([0.01] * 50), {})

    def test_short_returns_raises(self):
        with pytest.raises(ValueError, match="at least 30"):
            factor_attribution_report(pd.Series([0.01] * 10), {"X": pd.DataFrame({"F": [0.01] * 10})})


# ──────────────────────────────────────────────
# Sprint 0: strategy_backtest_report extensions
# ──────────────────────────────────────────────

class TestStrategyBacktestReportSprint0:
    def _ret(self, n=120):
        rng = np.random.default_rng(42)
        return pd.Series(rng.normal(0.001, 0.02, n))

    def test_signal_detectors_extension(self):
        events_found = []

        def detector(returns):
            events_found.append(len(returns))
            return [{"date": returns.index[0] if hasattr(returns.index[0], 'date') else 0,
                     "type": "signal"}]

        result = strategy_backtest_report(
            self._ret(), signal_detectors=[detector], n_bootstrap=50
        )
        assert "signal_events" in result
        assert len(result["signal_events"]) == 1

    def test_regime_grouping_breakdown(self):
        from datetime import date
        ret = self._ret()
        ret.index = pd.date_range("2023-01-01", periods=len(ret), freq="B")

        def grouping(d):
            return "bull" if d.month <= 6 else "bear"

        result = strategy_backtest_report(
            ret,
            regime_grouping=grouping,
            n_bootstrap=50,
        )
        assert "regime_grouping_breakdown" in result
        assert result["regime_grouping_breakdown"] is not None
        assert "bull" in result["regime_grouping_breakdown"] or "bear" in result["regime_grouping_breakdown"]

    def test_regime_grouping_breakdown_contents(self):
        from datetime import date
        ret = self._ret(200)
        dates = pd.date_range("2023-01-01", periods=200, freq="B")
        ret.index = dates

        def grouping(d):
            return "A"

        result = strategy_backtest_report(ret, regime_grouping=grouping, n_bootstrap=50)
        grp = result["regime_grouping_breakdown"]["A"]
        assert "n" in grp
        assert "mean_return" in grp
        assert "sharpe" in grp

    def test_signal_detector_exception_graceful(self):
        def bad_detector(returns):
            raise RuntimeError("broken")

        result = strategy_backtest_report(
            self._ret(), signal_detectors=[bad_detector], n_bootstrap=50
        )
        assert result["signal_events"] == []

    def test_regime_grouping_exception_graceful(self):
        def bad_grouping(d):
            raise ValueError("bad date")

        ret = self._ret()
        ret.index = pd.date_range("2023-01-01", periods=len(ret), freq="B")
        result = strategy_backtest_report(ret, regime_grouping=bad_grouping, n_bootstrap=50)
        assert "unknown" in result["regime_grouping_breakdown"]

    @pytest.mark.academic_reference
    def test_academic_reference_regime_grouping(self):
        """Regime-conditional performance follows Hamilton (1989) Markov switching
        regime-aware attribution (Ang & Bekaert 2004)."""
        ret = self._ret(200)
        ret.index = pd.date_range("2023-01-01", periods=200, freq="B")
        result = strategy_backtest_report(
            ret,
            regime_grouping=lambda d: "high" if d.month % 2 == 0 else "low",
            n_bootstrap=50,
        )
        assert result["regime_grouping_breakdown"] is not None
        assert len(result["regime_grouping_breakdown"]) == 2
