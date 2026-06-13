"""omodul.variant_generation_workflow — Generate question variants.

Composes oprim.generate_variant for multiple questions.
Enforces: variant.answer is always cleared after LLM generation (red line).

Pillars: fingerprint + decision_trail + cost
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class VariantGenerationConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "variant_generation_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"source_id", "variant_type"}

    variants_per_question: int = 3
    variant_type: str = "isomorphic"
    subject: str = "math"
    grade_level: int = 8
    model: str = "claude-sonnet-4-6"


class VariantSource(BaseModel):
    source_id: str = ""
    question: str
    answer: str = ""
    kc_ids: list[str] = []


class VariantGenerationInput(BaseModel):
    sources: list[VariantSource] = []


async def variant_generation_workflow(
    config: VariantGenerationConfig,
    input_data: VariantGenerationInput,
    output_dir: Path,
    *,
    caller: Any,
    on_step: Any = None,
) -> dict:
    from oprim.generate_variant import VariantInput, generate_variant

    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        trail.record(event="start", n_sources=len(input_data.sources))

        all_variants: list[dict] = []
        success_count = 0

        for src in input_data.sources:
            for _ in range(config.variants_per_question):
                inp = VariantInput(
                    original_question=src.question,
                    original_answer=src.answer,
                    kc_ids=src.kc_ids,
                    variant_type=config.variant_type,
                    grade_level=config.grade_level,
                    subject=config.subject,
                )
                item = await generate_variant(inp, caller=caller, model=config.model)
                all_variants.append({
                    "source_id": src.source_id,
                    "question": item.question,
                    "answer": item.answer,
                    "kernel_verified": item.kernel_verified,
                    "success": item.success,
                })
                if item.success:
                    success_count += 1

            trail.record(event="source_done", source_id=src.source_id)

            if on_step:
                on_step("variant_generation_workflow", src.source_id)

        fp = compute_fingerprint({
            "source_id": input_data.sources[0].source_id if input_data.sources else "",
            "variant_type": config.variant_type,
        })

        trail_path = trail.write(output_dir)
        trail.record(event="done", total=len(all_variants), success=success_count)

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            cost_usd=cost.total_usd,
            variants=all_variants,
            total_count=len(all_variants),
            success_count=success_count,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
        )
