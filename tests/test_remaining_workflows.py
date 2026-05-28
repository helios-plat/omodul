"""Tests for remaining_workflows omoduls (13)."""
import pytest
from omodul.remaining_workflows import *  # noqa: F403, F405

@pytest.fixture
def tmp(tmp_path):
    return tmp_path

def test_cross_timeframe(tmp):
    r = cross_timeframe_analysis(CrossTimeframeConfig(), {}, tmp)
    assert r["status"] == "completed"

def test_pack_promotion(tmp):
    r = pack_promotion_workflow(PackPromotionConfig(pack_id="v2"), {"new_score": 80, "baseline": 60}, tmp)
    assert r["status"] == "completed"

def test_regime_conditional(tmp):
    r = regime_conditional_analysis(RegimeConditionalConfig(), {"regime": "bull"}, tmp)
    assert r["status"] == "completed"

def test_cross_sectional(tmp):
    r = cross_sectional_ranking_workflow(CrossSectionalConfig(), {}, tmp)
    assert r["status"] == "completed"

def test_walk_forward(tmp):
    r = walk_forward_validation(WalkForwardConfig(strategy="momentum"), {}, tmp)
    assert r["status"] == "completed"

def test_capacity(tmp):
    r = strategy_capacity_assessment(CapacityConfig(strategy="fusion"), {}, tmp)
    assert r["status"] == "completed"

def test_failure_postmortem(tmp):
    r = signal_failure_postmortem(FailurePostmortemConfig(signal_id="s-123"), {"cause": "data_stale"}, tmp)
    assert r["findings"]["root_cause"] == "data_stale"

def test_decision_log(tmp):
    r = decision_log_correlation(DecisionLogConfig(user_id="u1"), {"decisions": [1, 2, 3]}, tmp)
    assert r["status"] == "completed"

def test_counterfactual(tmp):
    r = counterfactual_analysis(CounterfactualConfig(base_date="2024-01-01"), {"perturbations": {"vol": 0.5}}, tmp)
    assert r["status"] == "completed"

def test_insight(tmp):
    r = insight_generation(InsightConfig(), {}, tmp)
    assert r["status"] == "completed"

def test_cold_start(tmp):
    r = cold_start_briefing(ColdStartConfig(user_id="new_user"), {}, tmp)
    assert r["status"] == "completed"

def test_multi_source(tmp):
    r = multi_source_data_collection(MultiSourceConfig(sources=["binance", "coingecko"]), {}, tmp)
    assert r["findings"]["sources_collected"] == 2

def test_ohlcv_merge(tmp):
    r = ohlcv_merge_workflow(OhlcvMergeConfig(), {}, tmp)
    assert r["findings"]["bars_merged"] == 200
