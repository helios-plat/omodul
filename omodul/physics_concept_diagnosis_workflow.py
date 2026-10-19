"""omodul.physics_concept_diagnosis_workflow — 物理概念优先诊断业务事务

标准签名: (config, input, output_dir, *, caller, on_step) -> dict
支柱: fingerprint + decision_trail + cost

U.19：FCI式概念优先范式第一步——诊断（第二步"认知冲突"由 services 层用
remediation 文本直接呈现，第三步"计算迁移"复用既有 force_analysis_workflow，
按红线本 omodul 不调用其它 omodul）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import (
    BaseConfig,
    CostTracker,
    Trail,
    build_result,
    compute_fingerprint,
)


class PhysicsConceptDiagnosisConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "physics_concept_diagnosis_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"ku_id", "subject_ref"}

    model: str = "claude-sonnet-4-6"


class PhysicsConceptDiagnosisInput(BaseModel):
    ku_name: str
    ku_id: str = ""
    user_id: str = ""


async def physics_concept_diagnosis_workflow(
    config: PhysicsConceptDiagnosisConfig,
    input_data: PhysicsConceptDiagnosisInput,
    output_dir: Path,
    *,
    caller: Any = None,
    on_step: Any = None,
) -> dict:
    """物理概念优先诊断业务事务。

    命中已知误解 → 生成 FCI 式二选一诊断题；无候选误解 → has_candidate=False，
    调用方应跳过诊断，直接进入计算迁移。

    Returns
    -------
    dict
        status, fingerprint, trail_path, cost_usd,
        has_candidate, misconception_id, remediation,
        scenario, option_a, option_b, misconception_option
    """
    from obase.provider_registry import ProviderRegistry
    from oskill import physics_concept_diagnosis

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
        trail.record(event="start", ku_id=input_data.ku_id, ku_name=input_data.ku_name)

        result = await physics_concept_diagnosis(
            ku_name=input_data.ku_name,
            ku_id=input_data.ku_id or None,
            caller=caller,
            model=config.model,
        )

        if on_step:
            on_step(
                "physics_concept_diagnosis_workflow",
                f"has_candidate::{result is not None}",
            )

        fp = compute_fingerprint(
            {"ku_id": input_data.ku_id, "subject_ref": input_data.user_id}
        )

        if result is None:
            trail.record(event="no_candidate")
            return build_result(
                status="ok",
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(output_dir),
                cost_usd=cost.total_usd,
                has_candidate=False,
                misconception_id=None,
                remediation=None,
                scenario=None,
                option_a=None,
                option_b=None,
                misconception_option=None,
            )

        trail.record(
            event="diagnostic_generated", misconception_id=result.misconception_id
        )

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(output_dir),
            cost_usd=cost.total_usd,
            has_candidate=True,
            misconception_id=result.misconception_id,
            remediation=result.remediation,
            scenario=result.scenario,
            option_a=result.option_a,
            option_b=result.option_b,
            misconception_option=result.misconception_option,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
            has_candidate=False,
            misconception_id=None,
            remediation=None,
            scenario=None,
            option_a=None,
            option_b=None,
            misconception_option=None,
        )


class _MockCaller:
    async def __call__(self, **kwargs: Any) -> dict:
        return {
            "content": (
                '{"scenario":"一个物体在光滑水平面上以恒定速度运动，不受任何水平方向的力。",'
                '"option_a":"物体会因为没有力推它而逐渐减速直至停止",'
                '"option_b":"物体会保持恒定速度一直运动下去",'
                '"misconception_option":"A"}'
            ),
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


__all__ = [
    "PhysicsConceptDiagnosisConfig",
    "PhysicsConceptDiagnosisInput",
    "physics_concept_diagnosis_workflow",
]
