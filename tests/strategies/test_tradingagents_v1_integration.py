"""Integration test: mock only multi_agent_consensus, real BOCPD + position_sizing."""
import pytest
import numpy as np
from unittest.mock import patch, AsyncMock

from omodul.strategies.tradingagents_v1 import tradingagents_v1


@pytest.fixture
def realistic_market_state():
    np.random.seed(42)
    n = 200
    btc_returns = np.random.randn(n) * 0.015
    btc_closes = list(60000 + np.cumsum(btc_returns * 60000))

    return {
        "symbols": ["BTC-USDT"],
        "current_prices": {"BTC-USDT": btc_closes[-1]},
        "features": {
            "returns_BTC-USDT": btc_returns,
            "closes_BTC-USDT": btc_closes,
            "change_24h_pct_BTC-USDT": (btc_closes[-1] - btc_closes[-25]) / btc_closes[-25],
            "volume_24h_usd_BTC-USDT": 2e9,
            "realized_vol_30d_BTC-USDT": float(np.std(btc_returns[-30:]) * np.sqrt(365)),
        },
        "capital_usd": 10000.0,
    }


@pytest.fixture
def config():
    return {
        "deepseek_api_key": "sk-test",
        "llm_weight": 0.4,
        "classic_weight": 0.6,
        "direction_threshold": 0.1,
        "target_vol_annual": 0.20,
        "hazard_rate": 1.0 / 250.0,
    }


@pytest.mark.asyncio
async def test_full_chain_with_real_classic_layers(realistic_market_state, config):
    """Mock only LLM consensus, run BOCPD + position_sizing for real."""
    mock_consensus = {
        "symbol": "BTC-USDT",
        "llm_factor": 0.5,
        "llm_confidence": 75,
        "llm_verdict": "long",
        "bull_output": {"confidence": 70},
        "bear_output": {"confidence": 30},
        "referee_output": {"factor_value": 0.5, "verdict": "long"},
        "total_cost_usd": 0.003,
        "total_input_tokens": 300,
        "total_output_tokens": 150,
        "elapsed_ms_total": 1500,
        "audit_evidence": {
            "stack_calls": [
                {"function": "oskill.llm_agent.bull_analyst", "args_hash": "x" * 16},
                {"function": "oskill.llm_agent.bear_analyst", "args_hash": "y" * 16},
                {"function": "oskill.llm_agent.referee", "args_hash": "z" * 16},
            ],
            "llm_reasoning_trace": "BULL...BEAR...REFEREE...",
            "llm_factor_dsl": '{"bull": 70, "bear": 30, "ref": 0.5}',
            "llm_consensus_votes": {"bull": 0.7, "bear": 0.3, "referee": 0.5},
            "llm_input_tokens": 300,
            "llm_output_tokens": 150,
            "llm_cost_usd": 0.003,
        },
    }

    with patch(
        "omodul.strategies.tradingagents_v1.multi_agent_consensus",
        new=AsyncMock(return_value=mock_consensus),
    ):
        result = await tradingagents_v1(realistic_market_state, config)

    assert result["dropped"] is False
    sig = result["signals"]["BTC-USDT"]

    # Direction depends on actual BOCPD output + LLM — accept any valid direction
    assert sig["direction"] in ("long", "neutral", "short")

    # classic_factor must be in [-1, +1] (real BOCPD ran)
    assert -1.0 <= sig["metadata"]["classic_factor"] <= 1.0

    # Position sizing actually ran
    pos = result["target_positions"]["BTC-USDT"]
    if sig["direction"] != "neutral":
        assert pos["target_notional_usd"] > 0

    # Audit stack_calls has real function names
    funcs = [c["function"] for c in result["audit_evidence"]["stack_calls"]]
    assert "oskill.regime.bocpd" in funcs
    assert "oskill.llm_agent.bull_analyst" in funcs
    if sig["direction"] != "neutral":
        assert "oskill.portfolio.position_sizing_vol_target" in funcs
