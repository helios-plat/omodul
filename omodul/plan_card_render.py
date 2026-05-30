"""omodul.plan_card_render — 计划卡片渲染.

Pillars: fingerprint + decision_trail
H1 compliant: calls oskills only (no sibling omodul).
Composition (B10 oskills):
  - oskill.candidate_universe_builder_v3  (sync)
  - oskill.similar_context_injector       (sync, LLM via ProviderRegistry)
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar, Set

from obase.cost_tracker import CostTracker
from pydantic import BaseModel, Field

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class PlanCardConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "plan_card_render"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[Set[str]] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[Set[str]] = {"symbol", "trade_date"}

    symbol: str
    trade_date: date
    top_n: int = 20
    context_top_k: int = 3
    context_template: str = "历史上相似市场环境: {context}\n当前标的: {symbol}"


class PlanCardInput(BaseModel):
    universe: list[dict[str, Any]] = Field(
        default_factory=list,
        description="候选池 dicts, 每个含 symbol + score 字段",
    )
    anchor_vec: list[float] = Field(
        default_factory=list,
        description="当前环境嵌入向量",
    )
    history_vecs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="历史环境 dicts: {label: str, vec: list[float]}",
    )
    outcome_stats: dict[str, Any] = Field(
        default_factory=dict,
        description="历史胜率/盈亏等统计",
    )


class PlanCardFindings(BaseModel):
    symbol: str
    trade_date: str
    candidate_rank: int | None = None
    candidate_score: float | None = None
    total_candidates: int = 0
    similar_contexts: list[str] = Field(default_factory=list)
    context_narrative: str = ""
    outcome_summary: dict[str, Any] = Field(default_factory=dict)


def compute_fingerprint_for(
    config: PlanCardConfig,
    input_data: PlanCardInput,
) -> str:
    return compute_fingerprint(config, input_data)


def plan_card_render(
    config: PlanCardConfig,
    input_data: PlanCardInput,
    output_dir: Path | None = None,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Render plan card with candidate ranking + similar-context injection.

    Returns dict with: findings, fingerprint, decision_trail, status, error.
    """
    from oskill.candidate_universe_builder_v3 import candidate_universe_builder_v3
    from oskill.similar_context_injector import similar_context_injector

    started_at = datetime.now(UTC)
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    fingerprint = compute_fingerprint_for(config, input_data)

    findings: PlanCardFindings | None = None
    error: dict[str, Any] | None = None
    status = "completed"
    trail: dict[str, Any] = {}

    try:
        # Step 1: candidate_universe_builder_v3
        t0 = datetime.now(UTC)
        pool_result = None
        candidate_rank: int | None = None
        candidate_score: float | None = None

        if input_data.universe:
            pool_result = candidate_universe_builder_v3(
                universe=input_data.universe,
                scoring_fn=lambda x: float(x.get("score", 0.0)),
                top_n=config.top_n,
            )
            for i, c in enumerate(pool_result.candidates):
                if c.get("symbol") == config.symbol:
                    candidate_rank = i + 1
                    candidate_score = c.get("score")
                    break

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="candidate_universe_builder_v3",
            inputs_summary={"universe_size": len(input_data.universe), "symbol": config.symbol},
            outputs_summary={
                "n_candidates": len(pool_result.candidates) if pool_result else 0,
                "symbol_rank": candidate_rank,
            },
            started_at=t0,
        )

        # Step 2: similar_context_injector
        t0 = datetime.now(UTC)
        similar_ctxs: list[str] = []
        context_narrative = ""

        if input_data.anchor_vec and input_data.history_vecs:
            history_tuples: list[tuple[str, list[float]]] = [
                (h["label"], h["vec"])
                for h in input_data.history_vecs
                if "label" in h and "vec" in h
            ]
            if history_tuples:
                try:
                    from obase import ProviderRegistry

                    llm = ProviderRegistry.get(category="llm", name=config.llm_provider)
                    template = config.context_template.replace("{symbol}", config.symbol)
                    ctx_result = similar_context_injector(
                        anchor_vec=input_data.anchor_vec,
                        history_vecs=history_tuples,
                        context_template=template,
                        llm_caller=llm,
                        top_k=config.context_top_k,
                    )
                    similar_ctxs = [m.get("label", "") for m in ctx_result.top_k_matches]
                    context_narrative = ctx_result.prompt_with_context
                except Exception:
                    pass

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="similar_context_injector",
            inputs_summary={"history_count": len(input_data.history_vecs)},
            outputs_summary={"similar_count": len(similar_ctxs)},
            started_at=t0,
        )

        findings = PlanCardFindings(
            symbol=config.symbol,
            trade_date=str(config.trade_date),
            candidate_rank=candidate_rank,
            candidate_score=candidate_score,
            total_candidates=len(pool_result.candidates) if pool_result else 0,
            similar_contexts=similar_ctxs,
            context_narrative=context_narrative,
            outcome_summary=input_data.outcome_stats,
        )

    except Exception as exc:
        status = "failed"
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }

    finally:
        trail = build_decision_trail(
            fingerprint=fingerprint,
            config=config,
            input_data=input_data,
            trail_steps=trail_steps,
            cost_tracker=cost_tracker,
            started_at=started_at,
            status=status,
            error=error,
        )
        if output_dir:
            (output_dir / "decision_trail.json").write_text(
                json.dumps(trail, indent=2, default=str), encoding="utf-8"
            )

    return {
        "findings": findings.model_dump() if findings else None,
        "fingerprint": fingerprint,
        "decision_trail": trail,
        "status": status,
        "error": error,
    }
