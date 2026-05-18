"""Test 3-agent orchestration with mocked agents."""
import pytest
from unittest.mock import AsyncMock, patch

from omodul.llm_workflows.multi_agent_consensus import multi_agent_consensus
from oskill.llm_client import LLMTimeout


def _bull_output(confidence=70, reasons=None):
    return {
        "role": "bull_analyst",
        "prompt_version": "test",
        "raw_content": "{}",
        "parsed": {},
        "confidence": confidence,
        "reasons": reasons or ["bull"],
        "counter_arguments": [],
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.001,
        "model_id": "deepseek-chat",
        "elapsed_ms": 500,
        "prompt_hash_hex": "a" * 64,
        "parse_failed": False,
    }


def _bear_output(confidence=40, reasons=None):
    return {
        "role": "bear_analyst",
        "prompt_version": "test",
        "raw_content": "{}",
        "parsed": {},
        "confidence": confidence,
        "reasons": reasons or ["bear"],
        "counter_arguments": [],
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.001,
        "model_id": "deepseek-chat",
        "elapsed_ms": 500,
        "prompt_hash_hex": "b" * 64,
        "parse_failed": False,
    }


def _ref_output(factor=0.5, verdict="long", confidence=75):
    return {
        "role": "referee",
        "prompt_version": "test",
        "raw_content": "{}",
        "parsed": {},
        "factor_value": factor,
        "verdict": verdict,
        "confidence": confidence,
        "reasoning": "test",
        "input_tokens": 150,
        "output_tokens": 80,
        "cost_usd": 0.0015,
        "model_id": "deepseek-chat",
        "elapsed_ms": 700,
        "prompt_hash_hex": "c" * 64,
        "parse_failed": False,
    }


def _market_state():
    return {
        "current_price": 60000,
        "change_24h_pct": 0.02,
        "volume_24h_usd": 1e9,
        "realized_vol_30d": 0.5,
        "recent_bars": [],
        "daily_closes": [58000, 59000, 60000],
    }


@pytest.mark.asyncio
async def test_consensus_happy():
    with patch(
        "omodul.llm_workflows.multi_agent_consensus.bull_analyst",
        new=AsyncMock(return_value=_bull_output(70)),
    ), patch(
        "omodul.llm_workflows.multi_agent_consensus.bear_analyst",
        new=AsyncMock(return_value=_bear_output(30)),
    ), patch(
        "omodul.llm_workflows.multi_agent_consensus.referee",
        new=AsyncMock(return_value=_ref_output(0.6, "long", 80)),
    ):
        result = await multi_agent_consensus(
            symbol="BTC-USDT",
            market_state=_market_state(),
            classic_factor=0.4,
            api_key="test",
        )

    assert result["symbol"] == "BTC-USDT"
    assert result["llm_factor"] == 0.6
    assert result["llm_verdict"] == "long"
    assert result["llm_confidence"] == 80
    assert len(result["audit_evidence"]["stack_calls"]) == 3
    assert abs(result["audit_evidence"]["llm_cost_usd"] - 0.0035) < 1e-9


@pytest.mark.asyncio
async def test_consensus_propagates_bull_fail():
    """If bull fails, whole consensus fails (no partial success)."""
    with patch(
        "omodul.llm_workflows.multi_agent_consensus.bull_analyst",
        new=AsyncMock(side_effect=LLMTimeout("bull")),
    ), patch(
        "omodul.llm_workflows.multi_agent_consensus.bear_analyst",
        new=AsyncMock(return_value=_bear_output(40)),
    ), patch(
        "omodul.llm_workflows.multi_agent_consensus.referee",
        new=AsyncMock(return_value=_ref_output()),
    ):
        with pytest.raises(LLMTimeout):
            await multi_agent_consensus(
                symbol="BTC-USDT",
                market_state=_market_state(),
                classic_factor=0.0,
                api_key="test",
            )


@pytest.mark.asyncio
async def test_consensus_propagates_referee_fail():
    with patch(
        "omodul.llm_workflows.multi_agent_consensus.bull_analyst",
        new=AsyncMock(return_value=_bull_output()),
    ), patch(
        "omodul.llm_workflows.multi_agent_consensus.bear_analyst",
        new=AsyncMock(return_value=_bear_output()),
    ), patch(
        "omodul.llm_workflows.multi_agent_consensus.referee",
        new=AsyncMock(side_effect=LLMTimeout("referee")),
    ):
        with pytest.raises(LLMTimeout):
            await multi_agent_consensus(
                symbol="BTC-USDT",
                market_state=_market_state(),
                classic_factor=0.0,
                api_key="test",
            )


@pytest.mark.asyncio
async def test_consensus_audit_evidence_structure():
    with patch(
        "omodul.llm_workflows.multi_agent_consensus.bull_analyst",
        new=AsyncMock(return_value=_bull_output(60, ["reason1"])),
    ), patch(
        "omodul.llm_workflows.multi_agent_consensus.bear_analyst",
        new=AsyncMock(return_value=_bear_output(40, ["bear_reason"])),
    ), patch(
        "omodul.llm_workflows.multi_agent_consensus.referee",
        new=AsyncMock(return_value=_ref_output(0.3, "long", 65)),
    ):
        result = await multi_agent_consensus(
            symbol="BTC-USDT",
            market_state=_market_state(),
            classic_factor=0.2,
            api_key="test",
        )

    ev = result["audit_evidence"]
    assert "stack_calls" in ev
    assert "llm_reasoning_trace" in ev
    assert "llm_factor_dsl" in ev
    assert "llm_consensus_votes" in ev

    votes = ev["llm_consensus_votes"]
    assert votes["bull"] == 0.6
    assert votes["bear"] == 0.4
    assert votes["referee"] == 0.3

    trace = ev["llm_reasoning_trace"]
    assert "BULL ANALYST" in trace
    assert "BEAR ANALYST" in trace
    assert "REFEREE" in trace


@pytest.mark.asyncio
async def test_consensus_parallel_bull_bear():
    """Verify bull + bear run in parallel (both tasks created before await)."""
    import asyncio

    call_order: list[str] = []

    async def slow_bull(**kwargs):
        call_order.append("bull_start")
        await asyncio.sleep(0.01)
        call_order.append("bull_end")
        return _bull_output()

    async def slow_bear(**kwargs):
        call_order.append("bear_start")
        await asyncio.sleep(0.01)
        call_order.append("bear_end")
        return _bear_output()

    with patch(
        "omodul.llm_workflows.multi_agent_consensus.bull_analyst",
        new=slow_bull,
    ), patch(
        "omodul.llm_workflows.multi_agent_consensus.bear_analyst",
        new=slow_bear,
    ), patch(
        "omodul.llm_workflows.multi_agent_consensus.referee",
        new=AsyncMock(return_value=_ref_output()),
    ):
        await multi_agent_consensus(
            symbol="BTC-USDT",
            market_state=_market_state(),
            classic_factor=0.0,
            api_key="test",
        )

    # Both start before either ends (parallel execution)
    assert call_order.index("bear_start") < call_order.index("bull_end")
    assert call_order.index("bull_start") < call_order.index("bear_end")
