"""omodul.llm_alpha_mine — LLM-driven alpha hypothesis miner with backtest gate.

Pillars: cost, decision_trail, fingerprint
Composites: oskill.llm_factor_debate + omodul.backtest_gate

⚠️  Fingerprint covers market_context + factor_hypothesis ONLY (not LLM output).
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


class LlmAlphaMineConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "llm_alpha_mine"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"cost", "decision_trail", "fingerprint"}
    _fingerprint_fields: ClassVar[set[str]] = {"market_context", "factor_hypothesis"}

    market_context: str
    factor_hypothesis: str = ""
    max_tokens: int = 512
    strategy_name: str = "llm_alpha"
    n_splits: int = 4
    pbo_threshold: float = 0.5


def llm_alpha_mine(
    data: Any,
    *,
    config: LlmAlphaMineConfig,
    llm_caller: Any,
    strategy_fn: Any = None,
) -> dict[str, Any]:
    """Mine an alpha hypothesis via LLM debate then gate it through backtesting.

    Fingerprint is computed over market_context + factor_hypothesis ONLY,
    before any LLM call, so the identity of the query is stable.

    Composites:
        1. oskill.llm_factor_debate — bull/bear/referee LLM debate.
        2. omodul.backtest_gate    — walk-forward + DSR + PBO gate.

    Args:
        data: Historical dataset for backtesting.
        config: LlmAlphaMineConfig.
        llm_caller: Injected async LLM caller.
        strategy_fn: Backtest strategy callable. None → placeholder.

    Returns:
        Result with ``fingerprint``, ``debate``, ``gate``, ``status``, ``consensus``.
    """
    from omodul.backtest_gate import BacktestGateConfig, backtest_gate  # noqa: PLC0415
    from oskill.llm_factor_debate import llm_factor_debate  # noqa: PLC0415

    trail = Trail()

    # Fingerprint: deterministic fields only — computed before LLM call
    fp = compute_fingerprint({
        "market_context": config.market_context,
        "factor_hypothesis": config.factor_hypothesis,
    })
    trail.record(event="fingerprint_computed", fingerprint=fp)

    # LLM debate (async run inside sync wrapper)
    debate = asyncio.run(
        llm_factor_debate(
            config.market_context,
            llm_caller=llm_caller,
            max_tokens=config.max_tokens,
            factor_hypothesis=config.factor_hypothesis,
        )
    )
    trail.record(event="debate_complete",
                 consensus=debate["consensus"],
                 confidence=debate["confidence"])

    consensus = debate["consensus"]
    confidence = debate["confidence"]

    if strategy_fn is None:
        _mock_sharpe = {"bullish": 0.8, "bearish": -0.3, "neutral": 0.1}.get(consensus, 0.0)

        def strategy_fn(train, test):  # noqa: E306
            return {"sharpe": _mock_sharpe * confidence}

    gate_config = BacktestGateConfig(
        strategy_name=config.strategy_name,
        n_splits=config.n_splits,
        pbo_threshold=config.pbo_threshold,
    )
    gate = backtest_gate(strategy_fn, data, config=gate_config)
    trail.record(event="gate_complete", gate_status=gate.get("gate_status"))

    return build_result(
        status="ok",
        trail=trail,
        cost_usd=0.0,
        fingerprint=fp,
        debate={
            "consensus": debate["consensus"],
            "confidence": debate["confidence"],
            "bull_argument": debate["bull_argument"],
            "bear_argument": debate["bear_argument"],
            "verdict": debate["verdict"],
        },
        gate=gate,
        gate_status=gate.get("gate_status"),
        consensus=consensus,
    )
