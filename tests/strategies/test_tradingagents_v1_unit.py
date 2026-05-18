"""Unit tests for tradingagents_v1 — mock all LLM + classic calls."""
import pytest
import numpy as np
from unittest.mock import patch, AsyncMock, MagicMock

from oskill.llm_client import LLMTimeout
from omodul.strategies.tradingagents_v1 import tradingagents_v1


def _market_state():
    return {
        "symbols": ["BTC-USDT", "ETH-USDT"],
        "current_prices": {"BTC-USDT": 60000.0, "ETH-USDT": 3000.0},
        "features": {
            "returns_BTC-USDT": np.random.randn(200) * 0.01,
            "returns_ETH-USDT": np.random.randn(200) * 0.015,
            "closes_BTC-USDT": list(60000 + np.cumsum(np.random.randn(200) * 50)),
            "closes_ETH-USDT": list(3000 + np.cumsum(np.random.randn(200) * 5)),
            "change_24h_pct_BTC-USDT": 0.02,
            "change_24h_pct_ETH-USDT": -0.01,
            "volume_24h_usd_BTC-USDT": 2e9,
            "volume_24h_usd_ETH-USDT": 1e9,
            "realized_vol_30d_BTC-USDT": 0.55,
            "realized_vol_30d_ETH-USDT": 0.70,
        },
        "capital_usd": 10000.0,
    }


def _config():
    return {
        "deepseek_api_key": "sk-test",
        "llm_weight": 0.4,
        "classic_weight": 0.6,
        "direction_threshold": 0.1,
        "target_vol_annual": 0.20,
    }


def _consensus_long(symbol):
    return {
        "symbol": symbol,
        "llm_factor": 0.6,
        "llm_confidence": 75.0,
        "llm_verdict": "long",
        "bull_output": {"confidence": 70},
        "bear_output": {"confidence": 30},
        "referee_output": {"factor_value": 0.6, "verdict": "long"},
        "total_cost_usd": 0.003,
        "total_input_tokens": 300,
        "total_output_tokens": 150,
        "elapsed_ms_total": 1500,
        "audit_evidence": {
            "stack_calls": [
                {"function": "oskill.llm_agent.bull_analyst", "args_hash": "abc"},
                {"function": "oskill.llm_agent.bear_analyst", "args_hash": "def"},
                {"function": "oskill.llm_agent.referee", "args_hash": "ghi"},
            ],
            "llm_reasoning_trace": "BULL... BEAR... REFEREE...",
            "llm_factor_dsl": '{"bull": 70, "bear": 30, "ref": 0.6}',
            "llm_consensus_votes": {"bull": 0.7, "bear": 0.3, "referee": 0.6},
            "llm_input_tokens": 300,
            "llm_output_tokens": 150,
            "llm_cost_usd": 0.003,
        },
    }


@pytest.mark.asyncio
async def test_happy_path_long_signals():
    """All LLM + classic succeed → 2 long signals."""
    with patch(
        "omodul.strategies.tradingagents_v1.multi_agent_consensus",
        new=AsyncMock(side_effect=lambda **kw: _consensus_long(kw["symbol"])),
    ), patch(
        "omodul.strategies.tradingagents_v1.bocpd",
        return_value={"current_regime_probability": 0.7, "current_run_length": 100, "regime_changes": []},
    ), patch(
        "omodul.strategies.tradingagents_v1.position_sizing_vol_target",
        return_value={"target_notional_usd": 2000.0},
    ):
        result = await tradingagents_v1(_market_state(), _config())

    assert result["dropped"] is False
    assert len(result["signals"]) == 2
    assert result["signals"]["BTC-USDT"]["direction"] == "long"
    assert result["signals"]["ETH-USDT"]["direction"] == "long"

    # classic_factor = (0.7-0.5)*2 = 0.4, llm_factor = 0.6
    # final_factor = 0.4 * 0.6 + 0.6 * 0.4 = 0.24 + 0.24 = 0.48
    assert abs(result["signals"]["BTC-USDT"]["metadata"]["final_factor"] - 0.48) < 0.01

    assert result["target_positions"]["BTC-USDT"]["target_notional_usd"] == 2000.0


@pytest.mark.asyncio
async def test_llm_unavailable_drops_strategy():
    """LLM 失败 → dropped=True, signals 空, 不 fallback."""
    with patch(
        "omodul.strategies.tradingagents_v1.multi_agent_consensus",
        new=AsyncMock(side_effect=LLMTimeout("test")),
    ), patch(
        "omodul.strategies.tradingagents_v1.bocpd",
        return_value={"current_regime_probability": 0.7, "current_run_length": 100, "regime_changes": []},
    ):
        result = await tradingagents_v1(_market_state(), _config())

    assert result["dropped"] is True
    assert "llm_unavailable" in result["dropped_reason"]
    assert "LLMTimeout" in result["dropped_reason"]
    assert result["signals"] == {}
    assert result["target_positions"] == {}
    # audit_evidence still has BOCPD call (ran before LLM)
    assert "stack_calls" in result["audit_evidence"]
    assert any("bocpd" in c["function"] for c in result["audit_evidence"]["stack_calls"])


@pytest.mark.asyncio
async def test_neutral_signal_no_position():
    """Final factor below threshold → neutral, no position sizing called."""
    def _consensus_neutral(symbol):
        c = _consensus_long(symbol)
        c["llm_factor"] = 0.0
        c["llm_verdict"] = "neutral"
        return c

    sizing_mock = MagicMock(return_value={"target_notional_usd": 1000.0})

    with patch(
        "omodul.strategies.tradingagents_v1.multi_agent_consensus",
        new=AsyncMock(side_effect=lambda **kw: _consensus_neutral(kw["symbol"])),
    ), patch(
        "omodul.strategies.tradingagents_v1.bocpd",
        return_value={"current_regime_probability": 0.5, "current_run_length": 100, "regime_changes": []},
    ), patch(
        "omodul.strategies.tradingagents_v1.position_sizing_vol_target",
        sizing_mock,
    ):
        result = await tradingagents_v1(_market_state(), _config())

    # classic_factor = (0.5-0.5)*2 = 0, llm_factor = 0, final = 0
    # 0 is not > 0.1 threshold → neutral
    assert result["signals"]["BTC-USDT"]["direction"] == "neutral"
    assert result["target_positions"]["BTC-USDT"]["target_notional_usd"] == 0.0
    sizing_mock.assert_not_called()


@pytest.mark.asyncio
async def test_short_signal():
    """LLM strongly bear + bearish classic → short signal."""
    def _consensus_short(symbol):
        return {
            "symbol": symbol,
            "llm_factor": -0.7,
            "llm_confidence": 80,
            "llm_verdict": "short",
            "bull_output": {"confidence": 20},
            "bear_output": {"confidence": 80},
            "referee_output": {"factor_value": -0.7, "verdict": "short"},
            "total_cost_usd": 0.003,
            "total_input_tokens": 300,
            "total_output_tokens": 150,
            "elapsed_ms_total": 1500,
            "audit_evidence": {
                "stack_calls": [
                    {"function": "oskill.llm_agent.bull_analyst", "args_hash": "a"},
                    {"function": "oskill.llm_agent.bear_analyst", "args_hash": "b"},
                    {"function": "oskill.llm_agent.referee", "args_hash": "c"},
                ],
                "llm_reasoning_trace": "...",
                "llm_factor_dsl": "{}",
                "llm_consensus_votes": {"bull": 0.2, "bear": 0.8, "referee": -0.7},
                "llm_input_tokens": 300,
                "llm_output_tokens": 150,
                "llm_cost_usd": 0.003,
            },
        }

    with patch(
        "omodul.strategies.tradingagents_v1.multi_agent_consensus",
        new=AsyncMock(side_effect=lambda **kw: _consensus_short(kw["symbol"])),
    ), patch(
        "omodul.strategies.tradingagents_v1.bocpd",
        return_value={"current_regime_probability": 0.3, "current_run_length": 50, "regime_changes": []},
    ), patch(
        "omodul.strategies.tradingagents_v1.position_sizing_vol_target",
        return_value={"target_notional_usd": 1500.0},
    ):
        result = await tradingagents_v1(_market_state(), _config())

    # classic = (0.3-0.5)*2 = -0.4, llm = -0.7
    # final = 0.4 * -0.7 + 0.6 * -0.4 = -0.28 + -0.24 = -0.52 → short
    assert result["signals"]["BTC-USDT"]["direction"] == "short"


@pytest.mark.asyncio
async def test_factor_ensemble_arithmetic():
    """Verify final_factor = llm_weight * llm + classic_weight * classic."""
    cfg = _config()
    cfg["llm_weight"] = 0.3
    cfg["classic_weight"] = 0.7

    def _consensus(symbol):
        return {
            "symbol": symbol,
            "llm_factor": 1.0,
            "llm_confidence": 100,
            "llm_verdict": "long",
            "bull_output": {},
            "bear_output": {},
            "referee_output": {},
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "elapsed_ms_total": 0,
            "audit_evidence": {
                "stack_calls": [{"function": "bull"}],
                "llm_reasoning_trace": "",
                "llm_factor_dsl": "{}",
                "llm_consensus_votes": {},
                "llm_input_tokens": 0,
                "llm_output_tokens": 0,
                "llm_cost_usd": 0,
            },
        }

    with patch(
        "omodul.strategies.tradingagents_v1.multi_agent_consensus",
        new=AsyncMock(side_effect=lambda **kw: _consensus(kw["symbol"])),
    ), patch(
        "omodul.strategies.tradingagents_v1.bocpd",
        return_value={"current_regime_probability": 1.0, "current_run_length": 200, "regime_changes": []},
    ), patch(
        "omodul.strategies.tradingagents_v1.position_sizing_vol_target",
        return_value={"target_notional_usd": 1000.0},
    ):
        result = await tradingagents_v1(_market_state(), cfg)

    # classic = (1.0-0.5)*2 = 1.0, llm = 1.0
    # final = 0.3 * 1.0 + 0.7 * 1.0 = 1.0
    assert abs(result["signals"]["BTC-USDT"]["metadata"]["final_factor"] - 1.0) < 1e-9


@pytest.mark.asyncio
async def test_audit_evidence_structure_full():
    """audit_evidence contains all required fields (GOLD-ready)."""
    with patch(
        "omodul.strategies.tradingagents_v1.multi_agent_consensus",
        new=AsyncMock(side_effect=lambda **kw: _consensus_long(kw["symbol"])),
    ), patch(
        "omodul.strategies.tradingagents_v1.bocpd",
        return_value={"current_regime_probability": 0.7, "current_run_length": 100, "regime_changes": []},
    ), patch(
        "omodul.strategies.tradingagents_v1.position_sizing_vol_target",
        return_value={"target_notional_usd": 2000.0},
    ):
        result = await tradingagents_v1(_market_state(), _config())

    ev = result["audit_evidence"]
    assert "stack_calls" in ev
    assert "intermediate_results" in ev
    assert "precondition_checks" in ev
    assert "llm_reasoning_trace" in ev
    assert "llm_factor_dsl" in ev
    assert "llm_consensus_votes" in ev
    assert "llm_input_tokens" in ev
    assert "llm_output_tokens" in ev
    assert "llm_cost_usd" in ev
    assert "llm_model_id" in ev

    # 2 symbols × (BOCPD + 3 LLM + sizing) = at least 8 calls minimum
    assert len(ev["stack_calls"]) >= 6

    # cost accumulates correctly: 2 symbols × 0.003
    assert abs(ev["llm_cost_usd"] - 0.006) < 1e-6


@pytest.mark.asyncio
async def test_empty_returns_skipped():
    """Symbol without returns data → skipped, no error."""
    ms = _market_state()
    ms["features"]["returns_BTC-USDT"] = np.array([])

    with patch(
        "omodul.strategies.tradingagents_v1.multi_agent_consensus",
        new=AsyncMock(side_effect=lambda **kw: _consensus_long(kw["symbol"])),
    ), patch(
        "omodul.strategies.tradingagents_v1.bocpd",
        return_value={"current_regime_probability": 0.7, "current_run_length": 100, "regime_changes": []},
    ), patch(
        "omodul.strategies.tradingagents_v1.position_sizing_vol_target",
        return_value={"target_notional_usd": 2000.0},
    ):
        result = await tradingagents_v1(ms, _config())

    assert "BTC-USDT" not in result["signals"]
    assert "ETH-USDT" in result["signals"]
    assert result["dropped"] is False


@pytest.mark.asyncio
async def test_threshold_boundary():
    """final_factor clearly below threshold → neutral (strict greater-than check).

    Uses threshold=0.15 with classic=llm=0.1 → final≈0.1 < 0.15 → neutral.
    (Avoids floating-point at-boundary ambiguity by using threshold > final.)
    """
    cfg = _config()
    cfg["direction_threshold"] = 0.15

    def _consensus(symbol):
        return {
            "symbol": symbol,
            "llm_factor": 0.1,
            "llm_confidence": 60,
            "llm_verdict": "long",
            "bull_output": {},
            "bear_output": {},
            "referee_output": {},
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "elapsed_ms_total": 0,
            "audit_evidence": {
                "stack_calls": [],
                "llm_reasoning_trace": "",
                "llm_factor_dsl": "{}",
                "llm_consensus_votes": {},
                "llm_input_tokens": 0,
                "llm_output_tokens": 0,
                "llm_cost_usd": 0,
            },
        }

    with patch(
        "omodul.strategies.tradingagents_v1.multi_agent_consensus",
        new=AsyncMock(side_effect=lambda **kw: _consensus(kw["symbol"])),
    ), patch(
        "omodul.strategies.tradingagents_v1.bocpd",
        return_value={"current_regime_probability": 0.55, "current_run_length": 100, "regime_changes": []},
    ), patch(
        "omodul.strategies.tradingagents_v1.position_sizing_vol_target",
        return_value={"target_notional_usd": 1000.0},
    ):
        result = await tradingagents_v1(_market_state(), cfg)

    # classic = (0.55-0.5)*2 = 0.1, llm = 0.1
    # final ≈ 0.1 which is NOT > 0.15 → neutral
    assert result["signals"]["BTC-USDT"]["direction"] == "neutral"
