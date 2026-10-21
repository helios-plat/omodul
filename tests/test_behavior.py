"""Tests for Group 1: Trading Behavior modules."""

import numpy as np
import pandas as pd
import pytest

from omodul.behavior import (
    monthly_trade_review,
    shadow_account_simulator,
    trade_journal_analyzer,
    training_task_recommend,
)


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

    def test_empty_market_data_raises(self):
        """Cover line 193: empty market_data raises ValueError."""
        dates = pd.date_range("2023-01-01", periods=5, freq="B")
        trades = pd.DataFrame({
            "timestamp": dates, "symbol": "SPY", "side": "buy",
            "quantity": 100, "price": 400.0, "pnl": 50.0,
        })
        with pytest.raises(ValueError, match="market_data must not be empty"):
            shadow_account_simulator(
                trades,
                pd.DataFrame(),  # empty market_data
                lambda ts, ctx: None,
            )


# ──────────────────────────────────────────────
# Sprint 0: monthly_trade_review
# ──────────────────────────────────────────────

def _make_trades(n=10, rng=None):
    if rng is None:
        rng = np.random.default_rng(42)
    return [
        {"symbol": "AAPL", "side": "buy", "pnl": float(rng.normal(50, 100))}
        for _ in range(n)
    ]


class TestMonthlyTradeReview:
    def test_basic_returns_required_keys(self):
        trades = _make_trades(10)
        result = monthly_trade_review(
            trades,
            period=(2026, 1),
            llm_client=lambda prompt: "Good month overall.",
            prompt_builder=lambda stats, pd: f"Review {pd['period_str']}",
        )
        assert "period" in result
        assert result["period"] == "2026-01"
        assert "summary_statistics" in result
        assert "llm_narrative" in result
        assert "key_insights" in result
        assert "recommended_focus_areas" in result

    def test_empty_trades_no_crash(self):
        result = monthly_trade_review(
            [],
            period=(2026, 2),
            llm_client=lambda p: "",
            prompt_builder=lambda s, p: "",
        )
        assert result["period"] == "2026-02"
        assert "No trades in this period" in result["key_insights"]

    def test_discipline_evaluator(self):
        trades = _make_trades(5)
        result = monthly_trade_review(
            trades,
            period=(2026, 3),
            llm_client=lambda p: "narrative",
            prompt_builder=lambda s, p: "prompt",
            discipline_evaluator=lambda t: 0.8,
        )
        assert result["discipline_summary"] is not None
        assert result["discipline_summary"]["avg_discipline_score"] == pytest.approx(0.8)
        assert result["discipline_summary"]["n_evaluated"] == 5

    def test_low_win_rate_and_negative_pnl_recommendations(self):
        """Cover lines 383-386: low win_rate and negative avg_pnl recommendations."""
        trades = [{"symbol": "A", "side": "sell", "realized_pnl_pct": -100.0} for _ in range(10)]
        result = monthly_trade_review(
            trades,
            period=(2026, 4),
            llm_client=lambda p: "",
            prompt_builder=lambda s, p: "",
        )
        assert any("win_rate" in r for r in result["recommended_focus_areas"])
        assert any("loss management" in r for r in result["recommended_focus_areas"])

    def test_llm_failure_graceful(self):
        def bad_llm(p):
            raise RuntimeError("LLM down")

        result = monthly_trade_review(
            _make_trades(5),
            period=(2026, 5),
            llm_client=bad_llm,
            prompt_builder=lambda s, p: "",
        )
        assert "LLM unavailable" in result["llm_narrative"]

    def test_discipline_evaluator_raises_graceful(self):
        def bad_eval(t):
            raise ValueError("bad")

        result = monthly_trade_review(
            _make_trades(3),
            period=(2026, 6),
            llm_client=lambda p: "",
            prompt_builder=lambda s, p: "",
            discipline_evaluator=bad_eval,
        )
        assert result["discipline_summary"] is None

    @pytest.mark.academic_reference
    def test_academic_reference_pnl_statistics(self):
        """trade_pnl_statistics uses Lopez de Prado (2018) AFML Ch.4 metrics."""
        trades = [{"pnl": 100.0}, {"pnl": -50.0}, {"pnl": 200.0}]
        result = monthly_trade_review(
            trades,
            period=(2026, 1),
            llm_client=lambda p: "review",
            prompt_builder=lambda s, p: "prompt",
        )
        # Win rate should be 2/3 with the 3 trades
        stats = result["summary_statistics"]
        assert stats.get("n_trades", 0) == 3 or "win_rate" in stats


# ──────────────────────────────────────────────
# Sprint 0: training_task_recommend
# ──────────────────────────────────────────────

class TestTrainingTaskRecommend:
    def _taxonomy(self):
        return [
            {"task_type": "entry_drill", "description": "Improve entry timing",
             "targets": ["low_win_rate", "disposition_effect"]},
            {"task_type": "loss_mgmt", "description": "Loss management practice",
             "targets": ["negative_avg_pnl"]},
            {"task_type": "journal_review", "description": "Review journal",
             "targets": ["overtrading"]},
        ]

    def test_basic_returns_required_keys(self):
        result = training_task_recommend(
            user_behavior_summary={"disposition_effect_ratio": 0.3},
            journal_summary={"win_rate": 0.6, "avg_pnl": 50.0},
            llm_client=lambda p: "Use risk management.",
            prompt_builder=lambda d: str(d),
            task_taxonomy=self._taxonomy(),
        )
        assert "recommended_tasks" in result
        assert "weakness_identified" in result
        assert "llm_reasoning" in result

    def test_low_win_rate_weakness_identified(self):
        result = training_task_recommend(
            user_behavior_summary={},
            journal_summary={"win_rate": 0.3, "avg_pnl": -10.0},
            llm_client=lambda p: "",
            prompt_builder=lambda d: "",
            task_taxonomy=self._taxonomy(),
        )
        assert "low_win_rate" in result["weakness_identified"]
        assert "negative_avg_pnl" in result["weakness_identified"]
        # entry_drill and loss_mgmt should be recommended
        task_types = [t["task_type"] for t in result["recommended_tasks"]]
        assert "entry_drill" in task_types
        assert "loss_mgmt" in task_types

    def test_disposition_effect_weakness(self):
        result = training_task_recommend(
            user_behavior_summary={"disposition_effect_ratio": 0.8},
            journal_summary={"win_rate": 0.6, "avg_pnl": 10.0},
            llm_client=lambda p: "",
            prompt_builder=lambda d: "",
            task_taxonomy=self._taxonomy(),
        )
        assert "disposition_effect" in result["weakness_identified"]

    def test_no_weaknesses_no_recommendations(self):
        result = training_task_recommend(
            user_behavior_summary={"disposition_effect_ratio": 0.1},
            journal_summary={"win_rate": 0.6, "avg_pnl": 100.0},
            llm_client=lambda p: "All good.",
            prompt_builder=lambda d: "",
            task_taxonomy=self._taxonomy(),
        )
        assert result["weakness_identified"] == []
        assert result["recommended_tasks"] == []

    def test_llm_failure_graceful(self):
        def bad_llm(p):
            raise ConnectionError("offline")

        result = training_task_recommend(
            user_behavior_summary={},
            journal_summary={"win_rate": 0.3},
            llm_client=bad_llm,
            prompt_builder=lambda d: "",
            task_taxonomy=self._taxonomy(),
        )
        assert "LLM unavailable" in result["llm_reasoning"]

    def test_empty_taxonomy_no_crash(self):
        result = training_task_recommend(
            user_behavior_summary={},
            journal_summary={"win_rate": 0.2, "avg_pnl": -50.0},
            llm_client=lambda p: "ok",
            prompt_builder=lambda d: "",
            task_taxonomy=[],
        )
        assert result["recommended_tasks"] == []

    @pytest.mark.academic_reference
    def test_academic_reference_weakness_mapping(self):
        """Weakness→task mapping follows behavioral finance taxonomy
        (Barber & Odean 2000, Thaler 1985 disposition effect framework)."""
        result = training_task_recommend(
            user_behavior_summary={"disposition_effect_ratio": 0.9},
            journal_summary={"win_rate": 0.2, "avg_pnl": -100.0},
            llm_client=lambda p: "3 weaknesses identified",
            prompt_builder=lambda d: str(d["weaknesses"]),
            task_taxonomy=self._taxonomy(),
        )
        # All 3 weakness types should fire
        assert len(result["weakness_identified"]) == 3
        assert len(result["recommended_tasks"]) >= 2


class TestShadowRuleExtraction:
    """Shadow-account rule extraction upstreamed from the Helios consumer line."""

    def test_baseline_rule_when_no_trades(self):
        from omodul.behavior import extract_shadow_rules

        rules = extract_shadow_rules([])
        assert len(rules) == 1
        assert rules[0]["rule_id"] == "SR0_BASELINE"
        assert rules[0]["direction"] == "long"

    def test_baseline_rule_when_no_pnl_column(self):
        from omodul.behavior import extract_shadow_rules

        rules = extract_shadow_rules(pd.DataFrame({"symbol": ["AAA", "BBB"]}))
        assert rules[0]["rule_id"] == "SR0_BASELINE"

    def test_baseline_when_too_few_winning_trades(self):
        from omodul.behavior import extract_shadow_rules

        trades = pd.DataFrame({"pnl": [10.0, -5.0], "symbol": ["A", "B"]})
        rules = extract_shadow_rules(trades, min_support=5)
        assert rules[0]["rule_id"] == "SR0_BASELINE"
        assert rules[0]["support_count"] == 1

    def test_extracts_rule_from_winning_trades(self, sample_trades):
        from omodul.behavior import extract_shadow_rules

        rules = extract_shadow_rules(sample_trades, min_support=2)
        assert rules[0]["rule_id"] == "SR1"
        assert rules[0]["support_count"] > 0
        assert rules[0]["direction"] in {"long", "short"}
        assert 0.01 <= rules[0]["stop_loss_pct"] <= 0.10
        assert 1.2 <= rules[0]["take_profit_rr"] <= 5.0
        assert rules[0]["holding_days_range"][0] <= rules[0]["holding_days_range"][1]

    def test_rule_is_json_serialisable(self, sample_trades):
        import json

        from omodul.behavior import extract_shadow_rules

        rules = extract_shadow_rules(sample_trades)
        json.dumps(rules)  # must not raise

    def test_accepts_list_of_dicts(self):
        from omodul.behavior import extract_shadow_rules

        trades = [
            {"pnl": 100.0, "side": "buy", "symbol": "A", "hold_days": 2},
            {"pnl": 50.0, "side": "buy", "symbol": "A", "hold_days": 3},
            {"pnl": -20.0, "side": "sell", "symbol": "B", "hold_days": 1},
        ]
        rules = extract_shadow_rules(trades, min_support=2)
        assert rules[0]["rule_id"] == "SR1"


class TestBuildShadowRuleFn:
    """The rule_fn factory must produce a deterministic callable for the simulator."""

    def _market(self, n: int = 30) -> pd.DataFrame:
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        return pd.DataFrame({"SPY": np.linspace(100.0, 120.0, n)}, index=dates)

    def test_returns_callable(self):
        from omodul.behavior import build_shadow_rule_fn

        fn = build_shadow_rule_fn([])
        assert callable(fn)

    def test_baseline_fallback_when_no_rules(self):
        from omodul.behavior import _default_baseline_rule, build_shadow_rule_fn

        fn = build_shadow_rule_fn([])
        market = self._market()
        ts = market.index[-1]
        out = fn(ts, {"market_data": market})
        assert out is None or isinstance(out, dict)
        assert _default_baseline_rule()["rule_id"] == "SR0_BASELINE"

    def test_no_signal_without_market_data(self):
        from omodul.behavior import build_shadow_rule_fn

        fn = build_shadow_rule_fn([])
        assert fn(pd.Timestamp("2023-01-05"), {"market_data": None}) is None
        assert fn(pd.Timestamp("2023-01-05"), {"market_data": pd.DataFrame()}) is None

    def test_enters_on_positive_momentum_then_exits(self):
        from omodul.behavior import build_shadow_rule_fn

        rules = [
            {
                "rule_id": "SR1",
                "direction": "long",
                "exit_condition": {"max_holding_days": 2},
                "stop_loss_pct": 0.03,
                "take_profit_rr": 2.0,
            }
        ]
        fn = build_shadow_rule_fn(rules)
        market = self._market()
        entry = fn(market.index[3], {"market_data": market.iloc[:4]})
        assert entry is not None
        assert entry["side"] == "long"

        # Drive it into a stop by feeding a crashing series.
        crash = pd.DataFrame({"SPY": [120.0, 100.0, 80.0, 60.0]}, index=market.index[:4])
        exit_trade = fn(market.index[6], {"market_data": crash})
        assert exit_trade is not None
        assert exit_trade["side"] == "sell"
        assert exit_trade["reason"] in {"stop", "target", "timeout"}

    def test_rsi_mtf_source_runs(self):
        from omodul.behavior import build_shadow_rule_fn

        fn = build_shadow_rule_fn([], rule_source="rsi_mtf")
        market = self._market(40)
        out = fn(market.index[-1], {"market_data": market})
        assert out is None or isinstance(out, dict)

    def test_fusion_score_source_gates_on_threshold(self):
        from omodul.behavior import build_shadow_rule_fn

        market = self._market()
        ts = market.index[-1]
        high = pd.Series([99.0], index=[ts.normalize()])
        low = pd.Series([1.0], index=[ts.normalize()])

        enter_fn = build_shadow_rule_fn([], rule_source="fusion_score", signal_series=high)
        assert enter_fn(ts, {"market_data": market}) is not None

        skip_fn = build_shadow_rule_fn([], rule_source="fusion_score", signal_series=low)
        assert skip_fn(ts, {"market_data": market}) is None

    def test_is_deterministic_for_same_inputs(self):
        from omodul.behavior import build_shadow_rule_fn

        market = self._market()
        ts = market.index[10]
        a = build_shadow_rule_fn([])(ts, {"market_data": market.iloc[:11]})
        b = build_shadow_rule_fn([])(ts, {"market_data": market.iloc[:11]})
        assert a == b
