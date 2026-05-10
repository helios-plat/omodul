"""Tests for Group 1: Trading Behavior modules."""

import numpy as np
import pandas as pd
import pytest

from omodul.behavior import shadow_account_simulator, trade_journal_analyzer


class TestTradeJournalAnalyzer:
    def test_basic_analysis(self, sample_trades):
        result = trade_journal_analyzer(sample_trades)
        assert "diagnostics" in result
        assert "behavior_metrics" in result
        assert result["behavior_metrics"]["n_trades_total"] == 100

    def test_disposition_effect(self, sample_trades):
        result = trade_journal_analyzer(sample_trades, diagnostics=["disposition"])
        assert "disposition" in result["diagnostics"]
        d = result["diagnostics"]["disposition"]
        assert "de_score" in d
        assert d["interpretation"] in ("strong", "moderate", "none")

    def test_overtrading(self, sample_trades):
        result = trade_journal_analyzer(sample_trades, diagnostics=["overtrading"])
        assert "overtrading" in result["diagnostics"]
        assert "turnover_ratio" in result["diagnostics"]["overtrading"]

    def test_with_benchmark(self, sample_trades, spy_returns):
        result = trade_journal_analyzer(sample_trades, benchmark_returns=spy_returns,
                                        diagnostics=["chasing"])
        assert "chasing" in result["diagnostics"]

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            trade_journal_analyzer(pd.DataFrame(columns=["timestamp", "symbol", "side", "quantity", "price"]))

    def test_missing_columns_raises(self):
        with pytest.raises(ValueError, match="columns"):
            trade_journal_analyzer(pd.DataFrame({"a": [1]}))

    def test_real_data_dogfood(self, sample_trades):
        """Real data integration test - no mocks."""
        result = trade_journal_analyzer(sample_trades, bootstrap_ci=True, n_bootstrap=100)
        assert result["behavior_metrics"]["n_trades_total"] > 0
        assert isinstance(result["summary_report"]["primary_biases"], list)


class TestShadowAccountSimulator:
    def test_basic_simulation(self):
        dates = pd.date_range("2023-01-01", periods=50, freq="B")
        trades = pd.DataFrame({
            "timestamp": dates[:20], "symbol": "SPY", "side": "buy",
            "quantity": 100, "price": 400.0, "pnl": np.random.default_rng(42).normal(50, 100, 20),
        })
        market = pd.DataFrame({"SPY": np.cumsum(np.random.default_rng(42).normal(0, 1, 50)) + 400}, index=dates)
        rule_fn = lambda ts, ctx: {"pnl": 10.0}
        result = shadow_account_simulator(trades, market, rule_fn)
        assert "actual_performance" in result
        assert "shadow_performance" in result
        assert "comparison" in result

    def test_empty_trades_raises(self):
        with pytest.raises(ValueError, match="empty"):
            shadow_account_simulator(
                pd.DataFrame(columns=["timestamp", "symbol", "side", "quantity", "price"]),
                pd.DataFrame({"SPY": [1, 2]}, index=pd.date_range("2023-01-01", periods=2)),
                lambda ts, ctx: None,
            )

    def test_with_regime_labels(self):
        dates = pd.date_range("2023-01-01", periods=60, freq="B")
        trades = pd.DataFrame({
            "timestamp": dates[:10], "symbol": "SPY", "side": "buy",
            "quantity": 100, "price": 400.0, "pnl": np.ones(10) * 50,
        })
        market = pd.DataFrame({"SPY": np.arange(60) + 400.0}, index=dates)
        labels = pd.Series(["BULL"] * 30 + ["BEAR"] * 30, index=dates)
        result = shadow_account_simulator(trades, market, lambda ts, ctx: {"pnl": 5}, regime_labels=labels)
        assert "regime_breakdown" in result

    def test_no_pnl_column_warns(self):
        """Missing pnl column warns."""
        dates = pd.date_range("2023-01-01", periods=30, freq="B")
        trades = pd.DataFrame({
            "timestamp": dates[:5], "symbol": "SPY", "side": "buy",
            "quantity": 100, "price": 400.0,
        })
        market = pd.DataFrame({"SPY": np.arange(30) + 400.0}, index=dates)
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            shadow_account_simulator(trades, market, lambda ts, ctx: None)
            assert any("pnl" in str(x.message) for x in w)

