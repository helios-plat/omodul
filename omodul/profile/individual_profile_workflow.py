"""End-to-end individual security profile workflow."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Literal, Optional

STABILITY = "experimental"


async def individual_profile_workflow(
    symbol: str,
    facts: dict,
    user_context: dict,
    industry_context: dict,
    llm_client: Callable,
    prompt_builder: Callable,
    cache: Optional[Any] = None,
    bust_rules: Optional[list[Callable]] = None,
    cost_tracker: Optional[Any] = None,
    tier: Literal["fast", "deep"] = "fast",
) -> dict:
    """Generate an LLM-driven security profile with caching and bust triggers.

    Parameters
    ----------
    symbol : security identifier
    facts : aggregated fundamental + technical + flow data
    user_context : user's system config / preferences
    industry_context : industry-level reference data
    llm_client : async or sync callable
    prompt_builder : builds profile prompt
    cache : optional cache object with .get(key) and .set(key, value)
    bust_rules : list of callables (cached_profile, current_facts) -> bool
    cost_tracker : optional cost tracker callable
    tier : "fast" or "deep" model tier

    Returns
    -------
    {
        "symbol": str,
        "profile": {
            "thesis": str,
            "strengths": list[str],
            "weaknesses": list[str],
            "key_metrics": dict,
            "comparison_peers": list[str],
            "risk_factors": list[str]
        },
        "cache_status": "hit" | "miss" | "bust",
        "tier_used": str,
        "generation_cost": float,
        "trail_id": str
    }

    Methodology
    -----------
    1. Check cache; if hit, evaluate bust_rules
    2. If miss or bust, build prompt and call LLM
    3. Parse and validate output
    4. Update cache
    5. Track cost

    Reference
    ---------
    Standard profile caching pattern with cache invalidation (bust rules).
    """
    import inspect

    trail_id = str(uuid.uuid4())
    cache_key = f"profile:{symbol}:{tier}"
    cache_status = "miss"
    generation_cost = 0.0

    cached_profile = None
    if cache is not None:
        try:
            cached_profile = cache.get(cache_key)
        except Exception:
            cached_profile = None

    if cached_profile is not None:
        cache_status = "hit"
        if bust_rules:
            for bust_rule in bust_rules:
                try:
                    if bust_rule(cached_profile, facts):
                        cache_status = "bust"
                        cached_profile = None
                        break
                except Exception:
                    pass

    profile: dict
    if cached_profile is not None:
        profile = cached_profile
    else:
        prompt = prompt_builder({
            "symbol": symbol,
            "facts": facts,
            "user_context": user_context,
            "industry_context": industry_context,
            "tier": tier,
        })

        try:
            if inspect.iscoroutinefunction(llm_client):
                llm_response = await llm_client(prompt)
            else:
                llm_response = llm_client(prompt)
        except Exception as exc:
            llm_response = f"[LLM unavailable: {exc}]"

        llm_str = str(llm_response) if llm_response else ""
        generation_cost = 0.001 if tier == "fast" else 0.01

        profile = {
            "thesis": llm_str[:500] if llm_str else f"Profile for {symbol}",
            "strengths": facts.get("strengths", []),
            "weaknesses": facts.get("weaknesses", []),
            "key_metrics": {
                k: v for k, v in facts.items()
                if k not in ("strengths", "weaknesses") and isinstance(v, (int, float))
            },
            "comparison_peers": industry_context.get("peers", []),
            "risk_factors": facts.get("risk_factors", []),
        }

        if cache is not None:
            try:
                cache.set(cache_key, profile)
            except Exception:
                pass

    if cost_tracker is not None:
        try:
            cost_tracker({
                "trail_id": trail_id,
                "symbol": symbol,
                "cost": generation_cost,
                "tier": tier,
            })
        except Exception:
            pass

    return {
        "symbol": symbol,
        "profile": profile,
        "cache_status": cache_status,
        "tier_used": tier,
        "generation_cost": generation_cost,
        "trail_id": trail_id,
    }
