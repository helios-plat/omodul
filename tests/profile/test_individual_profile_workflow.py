"""Tests for individual_profile_workflow (Sprint 0)."""

from __future__ import annotations

import pytest

from omodul.profile.individual_profile_workflow import individual_profile_workflow

STABILITY = "experimental"


async def _simple_llm(prompt: str) -> str:
    return f"Strong buy thesis for {prompt[:20]}"


def _sync_llm(prompt: str) -> str:
    return f"Neutral thesis for {prompt[:20]}"


def _prompt_builder(ctx: dict) -> str:
    return f"Build profile for {ctx['symbol']}"


class TestIndividualProfileWorkflow:
    @pytest.mark.asyncio
    async def test_basic_async_llm(self):
        result = await individual_profile_workflow(
            symbol="AAPL",
            facts={"revenue_growth": 0.15, "pe_ratio": 25.0,
                   "strengths": ["brand"], "weaknesses": ["valuation"],
                   "risk_factors": ["macro"]},
            user_context={"risk_tolerance": "moderate"},
            industry_context={"peers": ["MSFT", "GOOGL"]},
            llm_client=_simple_llm,
            prompt_builder=_prompt_builder,
        )
        assert result["symbol"] == "AAPL"
        assert "profile" in result
        profile = result["profile"]
        assert "thesis" in profile
        assert "strengths" in profile
        assert "weaknesses" in profile
        assert "key_metrics" in profile
        assert "comparison_peers" in profile
        assert "risk_factors" in profile

    @pytest.mark.asyncio
    async def test_sync_llm(self):
        result = await individual_profile_workflow(
            symbol="MSFT",
            facts={"revenue_growth": 0.10},
            user_context={},
            industry_context={"peers": []},
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
        )
        assert result["symbol"] == "MSFT"
        assert result["tier_used"] == "fast"
        assert result["generation_cost"] == pytest.approx(0.001)

    @pytest.mark.asyncio
    async def test_deep_tier_higher_cost(self):
        result = await individual_profile_workflow(
            symbol="GOOG",
            facts={},
            user_context={},
            industry_context={},
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
            tier="deep",
        )
        assert result["tier_used"] == "deep"
        assert result["generation_cost"] == pytest.approx(0.01)

    @pytest.mark.asyncio
    async def test_cache_miss_then_hit(self):
        class DictCache:
            def __init__(self):
                self._s = {}
            def get(self, k):
                return self._s.get(k)
            def set(self, k, v):
                self._s[k] = v

        cache = DictCache()
        kwargs = dict(
            symbol="TSLA",
            facts={"revenue_growth": 0.3},
            user_context={},
            industry_context={"peers": []},
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
            cache=cache,
        )
        r1 = await individual_profile_workflow(**kwargs)
        assert r1["cache_status"] == "miss"
        r2 = await individual_profile_workflow(**kwargs)
        assert r2["cache_status"] == "hit"

    @pytest.mark.asyncio
    async def test_cache_bust_rule(self):
        class DictCache:
            def __init__(self):
                self._s = {}
            def get(self, k):
                return self._s.get(k)
            def set(self, k, v):
                self._s[k] = v

        cache = DictCache()
        facts = {"revenue_growth": 0.1}
        kwargs = dict(
            symbol="AMZN",
            facts=facts,
            user_context={},
            industry_context={},
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
            cache=cache,
        )
        r1 = await individual_profile_workflow(**kwargs)
        assert r1["cache_status"] == "miss"
        # bust rule: always bust
        r2 = await individual_profile_workflow(
            **kwargs, bust_rules=[lambda cached, facts: True]
        )
        assert r2["cache_status"] == "bust"

    @pytest.mark.asyncio
    async def test_llm_failure_graceful(self):
        async def bad_llm(prompt):
            raise ConnectionError("LLM down")

        result = await individual_profile_workflow(
            symbol="FAIL",
            facts={},
            user_context={},
            industry_context={},
            llm_client=bad_llm,
            prompt_builder=_prompt_builder,
        )
        assert "LLM unavailable" in result["profile"]["thesis"]

    @pytest.mark.asyncio
    async def test_key_metrics_extraction(self):
        facts = {
            "pe_ratio": 20.0,
            "revenue_growth": 0.15,
            "strengths": ["moat"],
            "weaknesses": [],
            "risk_factors": ["dilution"],
        }
        result = await individual_profile_workflow(
            symbol="META",
            facts=facts,
            user_context={},
            industry_context={"peers": []},
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
        )
        metrics = result["profile"]["key_metrics"]
        assert "pe_ratio" in metrics
        assert "revenue_growth" in metrics
        assert "strengths" not in metrics
        assert "weaknesses" not in metrics

    @pytest.mark.asyncio
    async def test_trail_id_unique(self):
        results = []
        for _ in range(3):
            r = await individual_profile_workflow(
                symbol="NVDA", facts={}, user_context={}, industry_context={},
                llm_client=_sync_llm, prompt_builder=_prompt_builder,
            )
            results.append(r["trail_id"])
        assert len(set(results)) == 3

    @pytest.mark.asyncio
    async def test_cost_tracker_called(self):
        tracked = []
        await individual_profile_workflow(
            symbol="AMD",
            facts={},
            user_context={},
            industry_context={},
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
            cost_tracker=lambda info: tracked.append(info),
        )
        assert len(tracked) == 1
        assert tracked[0]["symbol"] == "AMD"

    @pytest.mark.asyncio
    async def test_cache_set_error_graceful(self):
        class BadCache:
            def get(self, k):
                return None
            def set(self, k, v):
                raise IOError("disk full")

        result = await individual_profile_workflow(
            symbol="ORCL",
            facts={},
            user_context={},
            industry_context={},
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
            cache=BadCache(),
        )
        assert result["symbol"] == "ORCL"

    @pytest.mark.asyncio
    async def test_cache_get_error_graceful(self):
        """Cover lines 79-80: cache.get() raises, treated as miss."""
        class ErrorCache:
            def get(self, k):
                raise IOError("cache unavailable")
            def set(self, k, v):
                pass

        result = await individual_profile_workflow(
            symbol="ERRTEST",
            facts={},
            user_context={},
            industry_context={},
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
            cache=ErrorCache(),
        )
        assert result["cache_status"] == "miss"
        assert result["symbol"] == "ERRTEST"

    @pytest.mark.asyncio
    async def test_bust_rule_exception_graceful(self):
        """Cover lines 91-92: bust_rule raises, bust not triggered."""
        class DictCache:
            def __init__(self):
                self._s = {}
            def get(self, k):
                return self._s.get(k)
            def set(self, k, v):
                self._s[k] = v

        cache = DictCache()
        kwargs = dict(
            symbol="BUSTFAIL",
            facts={},
            user_context={},
            industry_context={},
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
            cache=cache,
        )
        # First call → miss, populate cache
        await individual_profile_workflow(**kwargs)
        # Second call with bad bust_rule → should not bust
        def bad_bust_rule(cached, facts):
            raise RuntimeError("bust rule error")

        result = await individual_profile_workflow(**kwargs, bust_rules=[bad_bust_rule])
        assert result["cache_status"] == "hit"  # still hit, bust failed gracefully

    @pytest.mark.asyncio
    async def test_cost_tracker_exception_graceful(self):
        """Cover lines 143-144: cost_tracker raises, no crash."""
        def bad_tracker(info):
            raise RuntimeError("tracker down")

        result = await individual_profile_workflow(
            symbol="TRACKFAIL",
            facts={},
            user_context={},
            industry_context={},
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
            cost_tracker=bad_tracker,
        )
        assert result["symbol"] == "TRACKFAIL"

    @pytest.mark.academic_reference
    @pytest.mark.asyncio
    async def test_academic_reference_profile_structure(self):
        """Individual profile workflow follows the LLM-assisted fundamental analysis
        framework of Kim et al. (2024) 'Large Language Models as Financial Analysts'."""
        result = await individual_profile_workflow(
            symbol="BRK",
            facts={"pe_ratio": 15.0, "roe": 0.18, "strengths": ["diversified"],
                   "weaknesses": ["complexity"], "risk_factors": ["succession"]},
            user_context={"experience": "expert"},
            industry_context={"peers": ["JPM", "GS"]},
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
        )
        profile = result["profile"]
        assert profile["comparison_peers"] == ["JPM", "GS"]
        assert profile["strengths"] == ["diversified"]
        assert profile["risk_factors"] == ["succession"]
        assert result["cache_status"] in ("hit", "miss", "bust")
