"""3-agent consensus workflow: bull + bear (parallel) → referee → factor.

Used by omodul.strategies.tradingagents_v1 (P16).
"""
from __future__ import annotations

import asyncio

import structlog

from omodul.llm_workflows._audit_evidence import build_audit_evidence
from oskill.llm_agent import bear_analyst, bull_analyst, referee
from oskill.llm_client import LLMUnavailable

log = structlog.get_logger(__name__)


async def multi_agent_consensus(
    *,
    symbol: str,
    market_state: dict,
    classic_factor: float,
    api_key: str,
    api_base: str = "https://api.deepseek.com/v1",
    model: str = "deepseek-chat",
) -> dict:
    """Run 3-agent consensus for one symbol.

    market_state required keys:
    - current_price: float
    - change_24h_pct: float
    - volume_24h_usd: float
    - realized_vol_30d: float
    - recent_bars: list[dict]
    - daily_closes: list[float]

    Returns
    -------
    dict
        {
            "symbol": str,
            "llm_factor": float,
            "llm_confidence": float,
            "llm_verdict": str,
            "bull_output": dict,
            "bear_output": dict,
            "referee_output": dict,
            "total_cost_usd": float,
            "total_input_tokens": int,
            "total_output_tokens": int,
            "elapsed_ms_total": int,
            "audit_evidence": dict,
        }

    Raises
    ------
    LLMUnavailable subclass — if any of 3 agents fails (no partial-success policy).
    """
    log.info("consensus_start", symbol=symbol, classic_factor=classic_factor)

    common_kwargs = dict(
        symbol=symbol,
        current_price=market_state["current_price"],
        change_24h_pct=market_state["change_24h_pct"],
        volume_24h_usd=market_state["volume_24h_usd"],
        realized_vol_30d=market_state["realized_vol_30d"],
        recent_bars=market_state["recent_bars"],
        daily_closes=market_state["daily_closes"],
        bocpd_factor=classic_factor,
        api_key=api_key,
        api_base=api_base,
        model=model,
    )

    # Bull and bear in parallel — no partial success
    try:
        bull_task = asyncio.create_task(bull_analyst(**common_kwargs))
        bear_task = asyncio.create_task(bear_analyst(**common_kwargs))
        bull_out, bear_out = await asyncio.gather(bull_task, bear_task)
    except LLMUnavailable:
        raise

    # Referee sequential after both succeed
    try:
        ref_out = await referee(
            symbol=symbol,
            bull_confidence=bull_out["confidence"],
            bull_reasons=bull_out["reasons"],
            bear_confidence=bear_out["confidence"],
            bear_reasons=bear_out["reasons"],
            classic_factor=classic_factor,
            api_key=api_key,
            api_base=api_base,
            model=model,
        )
    except LLMUnavailable:
        raise

    audit_evidence = build_audit_evidence(bull_out, bear_out, ref_out)

    log.info(
        "consensus_done",
        symbol=symbol,
        llm_factor=ref_out["factor_value"],
        llm_verdict=ref_out["verdict"],
        cost_usd=audit_evidence["llm_cost_usd"],
    )

    return {
        "symbol": symbol,
        "llm_factor": ref_out["factor_value"],
        "llm_confidence": ref_out["confidence"],
        "llm_verdict": ref_out["verdict"],
        "bull_output": bull_out,
        "bear_output": bear_out,
        "referee_output": ref_out,
        "total_cost_usd": audit_evidence["llm_cost_usd"],
        "total_input_tokens": audit_evidence["llm_input_tokens"],
        "total_output_tokens": audit_evidence["llm_output_tokens"],
        "elapsed_ms_total": audit_evidence["llm_elapsed_ms_total"],
        "audit_evidence": audit_evidence,
    }
