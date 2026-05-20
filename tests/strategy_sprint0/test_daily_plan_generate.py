"""Tests for daily_plan_generate (Sprint 0)."""

from __future__ import annotations

from datetime import date

import pytest

from omodul.strategy.daily_plan_generator import daily_plan_generate

STABILITY = "experimental"


def _prompt_builder(ctx: dict) -> str:
    return f"Plan for {ctx['regime']['state']}"


async def _async_llm(prompt: str) -> str:
    return f"Focus on momentum stocks. {prompt[:10]}"


def _sync_llm(prompt: str) -> str:
    return "Focus on value. Candidates look attractive."


def _filter_all(stock: dict) -> bool:
    return True


def _filter_none(stock: dict) -> bool:
    return False


class TestDailyPlanGenerate:
    @pytest.mark.asyncio
    async def test_basic_returns_required_keys(self):
        result = await daily_plan_generate(
            regime_state={"current_state": "BULL", "confidence": 0.8},
            themes=[{"theme_name": "AI"}, {"theme_name": "EV"}],
            scored_events=[{"symbol": "AAPL", "total_score": 75}],
            rotation_signals={"top_inflow_sectors": [{"sector": "Tech"}]},
            watchlist=[{"symbol": "AAPL", "sector": "Tech", "themes": ["AI"]}],
            holdings=[{"symbol": "MSFT", "pnl_pct": 0.05}],
            universe_filter=_filter_all,
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
        )
        assert "trade_date" in result
        assert "regime_summary" in result
        assert "candidate_stocks" in result
        assert "holdings_review" in result
        assert "key_themes_today" in result
        assert "llm_full_response" in result
        assert "trail_id" in result

    @pytest.mark.asyncio
    async def test_candidate_scoring(self):
        watchlist = [
            {"symbol": "AAPL", "sector": "Tech", "themes": ["AI"]},   # high_score_event + sector + theme → 65
            {"symbol": "GOOG", "sector": "Tech", "themes": []},       # sector only → 20
            {"symbol": "XOM", "sector": "Energy", "themes": []},      # no match → 0
        ]
        result = await daily_plan_generate(
            regime_state={"current_state": "BULL"},
            themes=[{"theme_name": "AI"}],
            scored_events=[{"symbol": "AAPL", "total_score": 80}],
            rotation_signals={"top_inflow_sectors": [{"sector": "Tech"}]},
            watchlist=watchlist,
            holdings=[],
            universe_filter=_filter_all,
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
        )
        symbols = [c["symbol"] for c in result["candidate_stocks"]]
        # AAPL should rank first
        assert symbols[0] == "AAPL"

    @pytest.mark.asyncio
    async def test_universe_filter_excludes(self):
        result = await daily_plan_generate(
            regime_state={"current_state": "BEAR"},
            themes=[],
            scored_events=[],
            rotation_signals={"top_inflow_sectors": []},
            watchlist=[{"symbol": "AAPL"}, {"symbol": "MSFT"}],
            holdings=[],
            universe_filter=_filter_none,
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
        )
        assert result["candidate_stocks"] == []

    @pytest.mark.asyncio
    async def test_holdings_review_logic(self):
        holdings = [
            {"symbol": "WIN", "pnl_pct": 0.25},   # consider_partial_exit
            {"symbol": "LOSS", "pnl_pct": -0.10}, # consider_exit
            {"symbol": "HOLD", "pnl_pct": 0.05},  # hold
        ]
        result = await daily_plan_generate(
            regime_state={"current_state": "NEUTRAL"},
            themes=[],
            scored_events=[],
            rotation_signals={"top_inflow_sectors": []},
            watchlist=[],
            holdings=holdings,
            universe_filter=_filter_all,
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
        )
        review = {r["symbol"]: r["action"] for r in result["holdings_review"]}
        assert review["WIN"] == "consider_partial_exit"
        assert review["LOSS"] == "consider_exit"
        assert review["HOLD"] == "hold"

    @pytest.mark.asyncio
    async def test_async_llm_client(self):
        result = await daily_plan_generate(
            regime_state={"current_state": "BULL"},
            themes=[],
            scored_events=[],
            rotation_signals={"top_inflow_sectors": []},
            watchlist=[],
            holdings=[],
            universe_filter=_filter_all,
            llm_client=_async_llm,
            prompt_builder=_prompt_builder,
        )
        assert len(result["llm_full_response"]) > 0

    @pytest.mark.asyncio
    async def test_llm_failure_graceful(self):
        def bad_llm(p):
            raise RuntimeError("network error")

        result = await daily_plan_generate(
            regime_state={"current_state": "BULL"},
            themes=[],
            scored_events=[],
            rotation_signals={"top_inflow_sectors": []},
            watchlist=[],
            holdings=[],
            universe_filter=_filter_all,
            llm_client=bad_llm,
            prompt_builder=_prompt_builder,
        )
        assert "LLM unavailable" in result["llm_full_response"]

    @pytest.mark.asyncio
    async def test_max_10_candidates(self):
        watchlist = [{"symbol": f"S{i}", "sector": "Tech", "themes": []} for i in range(20)]
        result = await daily_plan_generate(
            regime_state={"current_state": "BULL"},
            themes=[],
            scored_events=[],
            rotation_signals={"top_inflow_sectors": [{"sector": "Tech"}]},
            watchlist=watchlist,
            holdings=[],
            universe_filter=_filter_all,
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
        )
        assert len(result["candidate_stocks"]) <= 10

    @pytest.mark.asyncio
    async def test_candidate_stock_fields(self):
        watchlist = [{"symbol": "AAPL", "sector": "Tech", "themes": []}]
        result = await daily_plan_generate(
            regime_state={"current_state": "BULL"},
            themes=[],
            scored_events=[],
            rotation_signals={"top_inflow_sectors": []},
            watchlist=watchlist,
            holdings=[],
            universe_filter=_filter_all,
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
        )
        c = result["candidate_stocks"][0]
        assert "symbol" in c
        assert "rationale" in c
        assert "entry_condition" in c
        assert "size_suggestion" in c
        assert "stop_loss" in c
        assert "risk_level" in c

    @pytest.mark.asyncio
    async def test_universe_filter_exception_excluded(self):
        def bad_filter(stock):
            raise ValueError("filter error")

        result = await daily_plan_generate(
            regime_state={"current_state": "BULL"},
            themes=[],
            scored_events=[],
            rotation_signals={"top_inflow_sectors": []},
            watchlist=[{"symbol": "ERR"}],
            holdings=[],
            universe_filter=bad_filter,
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
        )
        assert result["candidate_stocks"] == []

    @pytest.mark.asyncio
    async def test_cost_tracker_called(self):
        tracked = []
        await daily_plan_generate(
            regime_state={"current_state": "BULL"},
            themes=[],
            scored_events=[],
            rotation_signals={"top_inflow_sectors": []},
            watchlist=[],
            holdings=[],
            universe_filter=_filter_all,
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
            cost_tracker=lambda info: tracked.append(info),
        )
        assert len(tracked) == 1

    @pytest.mark.asyncio
    async def test_cost_tracker_exception_graceful(self):
        """Cover lines 145-146: cost_tracker raises, no crash."""
        def bad_tracker(info):
            raise RuntimeError("tracker down")

        result = await daily_plan_generate(
            regime_state={"current_state": "BULL"},
            themes=[],
            scored_events=[],
            rotation_signals={"top_inflow_sectors": []},
            watchlist=[],
            holdings=[],
            universe_filter=_filter_all,
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
            cost_tracker=bad_tracker,
        )
        assert "trail_id" in result

    @pytest.mark.academic_reference
    @pytest.mark.asyncio
    async def test_academic_reference_regime_state(self):
        """Daily plan generation follows regime-aware position sizing:
        Ang (2014) 'Asset Management' Ch.7, sector rotation (Stovall 2006)."""
        watchlist = [
            {"symbol": "AAPL", "sector": "Tech", "themes": ["AI"]},
            {"symbol": "XOM", "sector": "Energy", "themes": []},
        ]
        result = await daily_plan_generate(
            regime_state={"current_state": "BULL", "confidence": 0.9},
            themes=[{"theme_name": "AI"}],
            scored_events=[{"symbol": "AAPL", "total_score": 90}],
            rotation_signals={"top_inflow_sectors": [{"sector": "Tech"}]},
            watchlist=watchlist,
            holdings=[],
            universe_filter=_filter_all,
            llm_client=_sync_llm,
            prompt_builder=_prompt_builder,
        )
        assert result["regime_summary"]["state"] == "BULL"
        assert result["regime_summary"]["confidence"] == pytest.approx(0.9)
        assert "AI" in result["key_themes_today"]
