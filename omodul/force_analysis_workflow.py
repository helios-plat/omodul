"""omodul.force_analysis_workflow — 物理受力分析引导业务事务

标准签名: (config, input, output_dir, *, caller, on_step) -> dict
支柱: fingerprint + decision_trail + cost

Added: omodul v1.30.7
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class ForceAnalysisConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "force_analysis_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"question_hash", "user_id"}

    max_turns: int = 15
    model: str = "claude-sonnet-4-6"


class ForceAnalysisInput(BaseModel):
    question_text: str
    student_messages: list[str] = []
    user_id: str = ""


async def force_analysis_workflow(
    config: ForceAnalysisConfig,
    input_data: ForceAnalysisInput,
    output_dir: Path,
    *,
    caller: Any = None,
    on_step: Any = None,
) -> dict:
    """物理受力分析引导业务事务。

    若 student_messages 为空 → 返回开场引导问（不含答案）。
    否则处理最新一条学生消息，返回下一个引导问题。

    Red line enforced at oskill layer: answer_leaked triggers safe fallback.

    Returns
    -------
    dict
        status, fingerprint, trail_path, cost_usd,
        assistant_text, equation_ready, answer_leaked
    """
    from obase.provider_registry import ProviderRegistry
    from oskill import physics_force_analysis_guide

    if caller is None:
        try:
            caller = ProviderRegistry.get().llm("default")
        except Exception:
            caller = _MockCaller()

    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        trail.record(event="start", user_id=input_data.user_id,
                     n_messages=len(input_data.student_messages))

        messages = input_data.student_messages[: config.max_turns]

        result = await physics_force_analysis_guide(
            question_text=input_data.question_text,
            student_messages=messages or None,
            caller=caller,
            model=config.model,
        )

        trail.record(
            event="guide_turn",
            equation_ready=result.equation_ready,
            answer_leaked=result.answer_leaked,
        )
        if on_step:
            on_step("force_analysis_workflow", f"reply::{result.assistant_text[:60]}")

        if result.answer_leaked:
            trail.record(event="redline_triggered")

        fp = compute_fingerprint({
            "question_hash": str(hash(input_data.question_text))[:12],
            "user_id": input_data.user_id,
        })

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(output_dir),
            cost_usd=cost.total_usd,
            assistant_text=result.assistant_text,
            equation_ready=result.equation_ready,
            answer_leaked=result.answer_leaked,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
            assistant_text="这道题你有什么初步想法？先说说物体的运动状态。",
            equation_ready=False,
            answer_leaked=False,
        )


class _MockCaller:
    async def __call__(self, **kwargs: Any) -> dict:
        return {
            "content": '{"assistant_text":"你觉得这个物体受几个力的作用？","equation_ready":false,"answer_leaked":false}',
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
