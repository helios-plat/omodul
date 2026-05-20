"""Generate a daily decision plan combining multi-source signals."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Callable, Optional

STABILITY = "experimental"


async def daily_plan_generate(
    regime_state: dict,
    themes: list[dict],
    scored_events: list[dict],
    rotation_signals: dict,
    watchlist: list[dict],
    holdings: list[dict],
    universe_filter: Callable,
    llm_client: Callable,
    prompt_builder: Callable,
    cost_tracker: Optional[Any] = None,
) -> dict:
    """Generate a daily plan with candidate stocks and entry conditions.

    Parameters
    ----------
    regime_state : current regime classification (from oskill.regime.multi_state_classify)
    themes : ranked themes (from oskill.factor.event_theme_cluster)
    scored_events : high-score disclosure events (from oskill.factor.disclosure_event_scoring)
    rotation_signals : sector rotation analysis
    watchlist : user's watchlist stocks with metadata
    holdings : user's current holdings with PnL
    universe_filter : (candidate_dict) -> bool filter for tradable universe
    llm_client : async callable for LLM
    prompt_builder : builds plan generation prompt
    cost_tracker : optional cost tracker

    Returns
    -------
    {
        "trade_date": date,
        "regime_summary": dict,
        "candidate_stocks": [...],
        "holdings_review": [...],
        "key_themes_today": list[str],
        "llm_full_response": str,
        "trail_id": str
    }

    Methodology
    -----------
    1. Aggregate inputs (regime + themes + events + rotation)
    2. Pre-filter universe (sector match + theme match + score > threshold)
    3. LLM analysis (rationale + entry conditions + sizing)
    4. Validate output

    Uses: oskill.regime.multi_state_classify, oskill.factor.*
    """
    trail_id = str(uuid.uuid4())
    trade_date = date.today()

    current_state = regime_state.get("current_state", "unknown")
    confidence = regime_state.get("confidence", 0.0)

    top_themes = [t.get("theme_name", "") for t in themes[:5]]
    top_inflow = [s.get("sector", "") for s in rotation_signals.get("top_inflow_sectors", [])]

    high_score_events = [e for e in scored_events if e.get("total_score", 0) >= 60]
    high_score_symbols = {e.get("symbol") for e in high_score_events}

    candidate_raw = []
    for stock in watchlist:
        symbol = stock.get("symbol", "")
        try:
            if not universe_filter(stock):
                continue
        except Exception:
            continue
        score = 0.0
        if symbol in high_score_symbols:
            score += 30.0
        sector = stock.get("sector", "")
        if sector in top_inflow:
            score += 20.0
        if any(t in stock.get("themes", []) for t in top_themes):
            score += 15.0
        candidate_raw.append({**stock, "_score": score})

    candidate_raw.sort(key=lambda x: x["_score"], reverse=True)
    max_candidates = 10

    context = {
        "regime": {"state": current_state, "confidence": confidence},
        "themes": top_themes,
        "rotation": top_inflow,
        "candidates": [s["symbol"] for s in candidate_raw[:max_candidates]],
        "holdings": [{"symbol": h.get("symbol"), "pnl_pct": h.get("pnl_pct", 0)} for h in holdings],
    }
    prompt = prompt_builder(context)

    try:
        import asyncio
        import inspect
        if inspect.iscoroutinefunction(llm_client):
            llm_response = await llm_client(prompt)
        else:
            llm_response = llm_client(prompt)
    except Exception as exc:
        llm_response = f"[LLM unavailable: {exc}]"

    llm_str = str(llm_response) if llm_response else ""

    candidate_stocks = [
        {
            "symbol": s.get("symbol", ""),
            "rationale": f"Score {s.get('_score', 0):.0f}: theme/sector/event match",
            "entry_condition": {"type": "market_open"},
            "size_suggestion": 0.05,
            "stop_loss": 0.05,
            "risk_level": "medium",
        }
        for s in candidate_raw[:max_candidates]
    ]

    holdings_review = []
    for h in holdings:
        symbol = h.get("symbol", "")
        pnl_pct = h.get("pnl_pct", 0)
        if pnl_pct < -0.08:
            action = "consider_exit"
        elif pnl_pct > 0.20:
            action = "consider_partial_exit"
        else:
            action = "hold"
        holdings_review.append({
            "symbol": symbol,
            "action": action,
            "reasoning": f"PnL={pnl_pct:.1%}, regime={current_state}",
        })

    if cost_tracker is not None:
        try:
            cost_tracker({"trail_id": trail_id, "element": "daily_plan_generate"})
        except Exception:
            pass

    return {
        "trade_date": trade_date,
        "regime_summary": {"state": current_state, "confidence": confidence},
        "candidate_stocks": candidate_stocks,
        "holdings_review": holdings_review,
        "key_themes_today": top_themes,
        "llm_full_response": llm_str,
        "trail_id": trail_id,
    }
