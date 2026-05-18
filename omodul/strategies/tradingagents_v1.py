"""LLM-driven multi-agent trading strategy (Phase 3 — 4th strategy).

Architecture:
  market_state + classic_factor (BOCPD)
       ↓
  multi_agent_consensus (3-agent LLM: bull/bear/referee)
       ↓
  factor ensemble (LLM weight + classic weight)
       ↓
  position sizing (vol target)
       ↓
  signals dict (per HELIVEX_STRATEGY_SCHEMA §6.4)

Failure mode: LLM unavailable → return dropped=True, layer 4 publisher
emits signal_dropped audit event with reason. NO fallback to classic (Cap 10 §2.5).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import structlog

from oprim.crypto import sha256_hash
from oprim.serialization import canonical_json
from oskill.llm_client import LLMUnavailable
from omodul.llm_workflows import multi_agent_consensus

try:
    from oskill.regime import bocpd as _bocpd_impl
except ImportError:
    _bocpd_impl = None

try:
    from oskill.portfolio import position_sizing_vol_target as _sizing_impl
except ImportError:
    _sizing_impl = None


log = structlog.get_logger(__name__)


# ── Fallbacks (match omodul/strategies/__init__.py convention) ──────────────

def _bocpd_fallback(
    returns: np.ndarray,
    hazard: float = 0.01,
    confidence_threshold: float = 0.6,
) -> dict:
    n = len(returns)
    prob = 1.0 - hazard ** max(1, n // 4)
    return {
        "current_regime_probability": min(prob, 0.99),
        "current_run_length": n,
        "regime_changes": [],
    }


def _sizing_fallback(
    signal_strength: float,
    instrument_vol_annual: float,
    portfolio_target_vol: float,
    current_capital: float,
    max_position_pct: float = 1.0,
) -> dict:
    if instrument_vol_annual <= 0:
        return {"target_notional_usd": 0.0, "fraction_of_capital": 0.0}
    fraction = min(
        signal_strength * (portfolio_target_vol / instrument_vol_annual),
        max_position_pct,
    )
    return {"target_notional_usd": fraction * current_capital, "fraction_of_capital": fraction}


bocpd = _bocpd_impl or _bocpd_fallback
position_sizing_vol_target = _sizing_impl or _sizing_fallback


# ── Helpers ─────────────────────────────────────────────────────────────────

def _short_hash(obj: Any) -> str:
    raw = canonical_json(obj)
    b = raw.encode() if isinstance(raw, str) else raw
    h = sha256_hash(b)
    return (h.hex() if isinstance(h, bytes) else h)[:16]


def _build_consensus_market_state(market_state: dict, symbol: str, recent_n: int = 24) -> dict:
    features = market_state.get("features", {})

    closes = list(features.get(f"closes_{symbol}", []))
    if not closes:
        returns = features.get(f"returns_{symbol}", np.array([]))
        if len(returns) > 0:
            cp = market_state["current_prices"][symbol]
            prices = [cp]
            for r in reversed(returns):
                prices.append(prices[-1] / math.exp(float(r)))
            prices.reverse()
            closes = prices

    recent_bars = []
    for i, c in enumerate(closes[-recent_n:]):
        recent_bars.append({
            "timestamp_ns": (i + 1) * 3_600_000_000_000,
            "data": {"open": c, "high": c, "low": c, "close": c, "volume": 0},
        })

    daily_closes = list(features.get(f"daily_closes_{symbol}", []))
    if not daily_closes and len(closes) >= 168:
        daily_closes = closes[-168::24]

    return {
        "current_price": float(market_state["current_prices"][symbol]),
        "change_24h_pct": float(features.get(f"change_24h_pct_{symbol}", 0.0)),
        "volume_24h_usd": float(features.get(f"volume_24h_usd_{symbol}", 1e8)),
        "realized_vol_30d": float(features.get(f"realized_vol_30d_{symbol}", 0.6)),
        "recent_bars": recent_bars,
        "daily_closes": daily_closes,
    }


# ── Strategy ─────────────────────────────────────────────────────────────────

async def tradingagents_v1(
    market_state: dict,
    config: dict,
) -> dict:
    """Phase 3 — 4th strategy: LLM 3-agent + classic BOCPD ensemble.

    Required market_state keys:
    - symbols: list[str]
    - current_prices: dict[str, float]
    - features: dict (per-symbol returns / closes / vol / etc.)
    - capital_usd: float

    Required config keys:
    - deepseek_api_key: str
    - deepseek_api_base: str (default https://api.deepseek.com/v1)
    - llm_model: str (default deepseek-chat)
    - llm_weight: float (default 0.4)
    - classic_weight: float (default 0.6)
    - direction_threshold: float (default 0.1)
    - target_vol_annual: float (default 0.20)
    - hazard_rate: float (default 1/250) for BOCPD

    Returns
    -------
    dict
        {
            "signals": dict[symbol, signal_dict],
            "target_positions": dict[symbol, position_dict],
            "execution_plans": dict[symbol, exec_dict],
            "audit_evidence": dict,
            "dropped": bool,
            "dropped_reason": str | None,
        }
    """
    symbols = market_state["symbols"]
    api_key = config["deepseek_api_key"]
    api_base = config.get("deepseek_api_base", "https://api.deepseek.com/v1")
    llm_model = config.get("llm_model", "deepseek-chat")
    llm_weight = float(config.get("llm_weight", 0.4))
    classic_weight = float(config.get("classic_weight", 0.6))
    direction_threshold = float(config.get("direction_threshold", 0.1))
    target_vol_annual = float(config.get("target_vol_annual", 0.20))
    hazard_rate = float(config.get("hazard_rate", 1.0 / 250.0))
    max_impact_bps = float(config.get("max_impact_bps", 30.0))

    if not math.isclose(llm_weight + classic_weight, 1.0, abs_tol=1e-6):
        log.warning(
            "ensemble_weights_not_normalized",
            llm_weight=llm_weight,
            classic_weight=classic_weight,
        )

    signals: dict[str, dict] = {}
    target_positions: dict[str, dict] = {}
    execution_plans: dict[str, dict] = {}
    all_stack_calls: list[dict] = []
    all_llm_traces: list[str] = []
    all_llm_dsls: list[str] = []
    all_consensus_votes: list[dict] = []
    total_cost = 0.0
    total_input_tokens = 0
    total_output_tokens = 0

    capital_usd = float(market_state.get("capital_usd", 10000.0))

    for symbol in symbols:
        features = market_state.get("features", {})
        returns_raw = features.get(f"returns_{symbol}")

        if returns_raw is None or len(returns_raw) == 0:
            log.warning("tradingagents_no_returns", symbol=symbol)
            continue

        returns = np.asarray(returns_raw, dtype=float)

        # 1. Classic BOCPD factor
        try:
            bocpd_result = bocpd(
                returns,
                hazard=hazard_rate,
                confidence_threshold=0.6,
            )
            # current_regime_probability ∈ [0, 1]; map to classic_factor ∈ [-1, +1]
            prob = float(bocpd_result.get("current_regime_probability", 0.5))
            classic_factor = max(-1.0, min(1.0, (prob - 0.5) * 2.0))
        except Exception as e:
            log.exception("bocpd_failed", symbol=symbol, error=str(e))
            classic_factor = 0.0

        all_stack_calls.append({
            "function": "oskill.regime.bocpd",
            "args_hash": _short_hash({"returns_len": int(len(returns)), "hazard": hazard_rate}),
        })

        # 2. LLM consensus
        consensus_market_state = _build_consensus_market_state(market_state, symbol)

        try:
            consensus = await multi_agent_consensus(
                symbol=symbol,
                market_state=consensus_market_state,
                classic_factor=classic_factor,
                api_key=api_key,
                api_base=api_base,
                model=llm_model,
            )
        except LLMUnavailable as e:
            log.warning(
                "tradingagents_llm_unavailable",
                symbol=symbol,
                error=str(e),
                error_class=type(e).__name__,
            )
            # Cap 10 §2.5: no fallback to classic — drop entire strategy
            return {
                "signals": {},
                "target_positions": {},
                "execution_plans": {},
                "audit_evidence": {
                    "stack_calls": all_stack_calls,
                    "intermediate_results": {
                        "llm_failure_symbol": symbol,
                        "llm_failure_class": type(e).__name__,
                    },
                    "precondition_checks": [f"llm_unavailable:{symbol}"],
                },
                "dropped": True,
                "dropped_reason": f"llm_unavailable:{type(e).__name__}:{e}",
            }

        # Accumulate audit evidence
        ae = consensus["audit_evidence"]
        all_stack_calls.extend(ae["stack_calls"])
        all_llm_traces.append(ae["llm_reasoning_trace"])
        all_llm_dsls.append(ae["llm_factor_dsl"])
        all_consensus_votes.append(ae["llm_consensus_votes"])
        total_cost += ae["llm_cost_usd"]
        total_input_tokens += ae["llm_input_tokens"]
        total_output_tokens += ae["llm_output_tokens"]

        # 3. Factor ensemble
        llm_factor = consensus["llm_factor"]
        final_factor = max(-1.0, min(1.0, llm_weight * llm_factor + classic_weight * classic_factor))

        # 4. Direction + strength
        if final_factor > direction_threshold:
            direction = "long"
        elif final_factor < -direction_threshold:
            direction = "short"
        else:
            direction = "neutral"

        strength = abs(final_factor)
        llm_confidence = consensus["llm_confidence"] / 100.0

        signals[symbol] = {
            "direction": direction,
            "strength": strength,
            "confidence": llm_confidence,
            "metadata": {
                "llm_factor": llm_factor,
                "classic_factor": classic_factor,
                "final_factor": final_factor,
                "llm_verdict": consensus["llm_verdict"],
                "llm_confidence": consensus["llm_confidence"],
                "ensemble_weights": {"llm": llm_weight, "classic": classic_weight},
            },
        }

        # 5. Position sizing
        if direction != "neutral":
            realized_vol = float(features.get(f"realized_vol_30d_{symbol}", 0.6))
            try:
                sizing = position_sizing_vol_target(
                    signal_strength=strength,
                    instrument_vol_annual=realized_vol,
                    portfolio_target_vol=target_vol_annual,
                    current_capital=capital_usd,
                    max_position_pct=1.0,
                )
                target_notional = float(sizing.get("target_notional_usd", 0.0))
                all_stack_calls.append({
                    "function": "oskill.portfolio.position_sizing_vol_target",
                    "args_hash": _short_hash({
                        "target_vol_annual": target_vol_annual,
                        "realized_vol": realized_vol,
                        "signal_strength": strength,
                        "direction": direction,
                        "capital_usd": capital_usd,
                    }),
                })
            except Exception:
                log.exception("position_sizing_failed", symbol=symbol)
                target_notional = 0.0
        else:
            target_notional = 0.0

        target_positions[symbol] = {
            "target_notional_usd": target_notional,
            "direction": direction,
        }

        execution_plans[symbol] = {
            "type": "market",
            "cost_model": "crypto_market_impact_sigmoid",
            "max_impact_bps": max_impact_bps,
        }

    # 6. Build audit_evidence (GOLD-ready)
    audit_evidence = {
        "stack_calls": all_stack_calls,
        "intermediate_results": {
            "llm_weight": llm_weight,
            "classic_weight": classic_weight,
            "direction_threshold": direction_threshold,
            "symbols_processed": list(symbols),
        },
        "precondition_checks": [
            f"consensus_completed:{symbol}" for symbol in symbols if symbol in signals
        ],
        "llm_reasoning_trace": "\n\n=====\n\n".join(all_llm_traces),
        "llm_factor_dsl": "[" + ",".join(all_llm_dsls) + "]",
        "llm_consensus_votes": all_consensus_votes,
        "llm_input_tokens": total_input_tokens,
        "llm_output_tokens": total_output_tokens,
        "llm_cost_usd": total_cost,
        "llm_model_id": llm_model,
    }

    return {
        "signals": signals,
        "target_positions": target_positions,
        "execution_plans": execution_plans,
        "audit_evidence": audit_evidence,
        "dropped": False,
        "dropped_reason": None,
    }
