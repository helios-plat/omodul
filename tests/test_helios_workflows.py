"""Tests for helios_workflows omoduls (9 new)."""
import pytest
from omodul.helios_workflows import *


class TestSignalFusionWorkflow:
    def test_completed(self, tmp_path):
        c = SignalFusionConfig(symbols=["BTC-USDT"])
        r = signal_fusion_workflow(c, {"raw_signals": {"trend": 0.8, "flow": 0.5}}, tmp_path)
        assert r["status"] == "completed"
        assert r["findings"]["fusion_score"] != 0

    def test_fingerprint(self, tmp_path):
        c = SignalFusionConfig(symbols=["BTC-USDT"])
        i = {"raw_signals": {"trend": 0.8}}
        assert signal_fusion_workflow(c, i, tmp_path)["fingerprint"] == signal_fusion_workflow(c, i, tmp_path)["fingerprint"]

    def test_trail_steps(self, tmp_path):
        c = SignalFusionConfig()
        r = signal_fusion_workflow(c, {"raw_signals": {"a": 1}}, tmp_path)
        assert len(r["decision_trail"]["steps"]) >= 3


class TestBacktestValidation:
    def test_completed(self, tmp_path):
        c = BacktestConfig(strategy="momentum", start_date="2024-01-01", end_date="2024-06-01")
        r = backtest_validation(c, {"equity_curve": [100, 110, 105, 120], "trades": [{}]}, tmp_path)
        assert r["status"] == "completed"
        assert r["findings"]["total_return"] > 0

    def test_empty_curve(self, tmp_path):
        c = BacktestConfig(strategy="x", start_date="a", end_date="b")
        r = backtest_validation(c, {"equity_curve": [100]}, tmp_path)
        assert r["status"] == "completed"


class TestUserFeedbackLoop:
    def test_accept(self, tmp_path):
        c = UserFeedbackConfig(user_id="u1", signal_id="s1")
        r = user_feedback_loop(c, {"action": "accept"}, tmp_path)
        assert r["status"] == "completed"
        assert r["findings"]["prior_updated"] is True

    def test_question(self, tmp_path):
        c = UserFeedbackConfig(user_id="u1", signal_id="s1")
        r = user_feedback_loop(c, {"action": "question"}, tmp_path)
        assert r["findings"]["prior_updated"] is False


class TestWhatIfScenario:
    def test_basic(self, tmp_path):
        c = WhatIfConfig()
        r = what_if_scenario(c, {"perturbations": {"trend": -0.5, "vol": 0.3}}, tmp_path)
        assert r["status"] == "completed"
        assert r["findings"]["count"] == 2


class TestKeyMomentTraversal:
    def test_basic(self, tmp_path):
        c = KeyMomentConfig(symbol="BTC-USDT")
        r = key_moment_traversal(c, {"moments": [{"ts": "2024-01-01"}]}, tmp_path)
        assert r["status"] == "completed"
        assert r["findings"]["moments_found"] == 1


class TestDataQualityPipeline:
    def test_basic(self, tmp_path):
        c = DataQualityConfig(sources=["binance", "coingecko"])
        r = data_quality_pipeline(c, {"sources": ["a", "b", "c"]}, tmp_path)
        assert r["status"] == "completed"


class TestAlertPersonalization:
    def test_basic(self, tmp_path):
        c = AlertPersonalizationConfig(user_id="u1")
        r = alert_personalization(c, {"preferences": {"threshold": 0.8}}, tmp_path)
        assert r["status"] == "completed"
        assert r["findings"]["personalized"] is True


class TestDecisionAuditTrail:
    def test_basic(self, tmp_path):
        c = DecisionAuditConfig(decision_id="d-123")
        r = decision_audit_trail(c, {}, tmp_path)
        assert r["status"] == "completed"
        assert r["findings"]["decision_id"] == "d-123"


class TestAbstainAwareRecommendation:
    def test_abstain(self, tmp_path):
        c = AbstainRecommendationConfig()
        r = abstain_aware_recommendation(c, {"confidence": 0.3, "fusion_score": 50}, tmp_path)
        assert r["findings"]["recommendation"] == "abstain"

    def test_buy(self, tmp_path):
        c = AbstainRecommendationConfig()
        r = abstain_aware_recommendation(c, {"confidence": 0.9, "fusion_score": 50}, tmp_path)
        assert r["findings"]["recommendation"] == "buy"

    def test_hold(self, tmp_path):
        c = AbstainRecommendationConfig()
        r = abstain_aware_recommendation(c, {"confidence": 0.9, "fusion_score": 5}, tmp_path)
        assert r["findings"]["recommendation"] == "hold"
