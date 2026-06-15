"""omodul.practice_workflow — Generate a practice set for a given KC.

Wraps oprim.generate_variant to produce `count` practice questions.
KC template is derived from data/guangdong_math_kc.py.

Pillars: fingerprint + decision_trail + cost
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint

# Template questions by KC module keyword (fallback if KC not found in dict)
_KC_TEMPLATES: dict[str, tuple[str, str]] = {
    "conic":      ("已知椭圆 x²/4 + y²/3 = 1，求过焦点的直线与椭圆交点坐标。", "GDMATH-CONIC-01"),
    "func":       ("求函数 f(x) = 2x² - 3x + 1 的最小值。", "GDMATH-FUNC-01"),
    "ineq":       ("解不等式 x² - 5x + 6 < 0。", "GDMATH-INEQ-01"),
    "trig":       ("已知 sin(α) = 3/5，α∈(0, π/2)，求 cos(2α)。", "GDMATH-TRIG-01"),
    "seq":        ("等差数列 {a_n} 中，a_1=2，公差 d=3，求 a_10。", "GDMATH-SEQ-01"),
    "prob":       ("从一批产品中随机取3件，求至少1件次品的概率。", "GDMATH-PROB-01"),
    "deriv":      ("求函数 f(x) = x³ - 3x² + 2 的极值。", "GDMATH-DERIV-01"),
    "set":        ("已知全集 U={1,2,3,4,5}，集合 A={1,3}，B={2,4}，求 A∪B 与 ∁ᵤA。", "GDMATH-SET-01"),
    "vector":     ("已知向量 a=(2,-1)，b=(1,3)，求 2a-b 及 a·b。", "GDMATH-VEC-01"),
    "stat":       ("一组数据 2,4,6,8,10，求平均数和方差。", "GDMATH-STAT-01"),
}


def _get_template(kc_id: str) -> tuple[str, str]:
    """Return (question_text, answer) template for given KC."""
    kc_lower = kc_id.lower()
    for key, (q, _) in _KC_TEMPLATES.items():
        if key in kc_lower:
            return q, ""
    return f"与知识点 {kc_id} 相关的数学练习题。", ""


class PracticeConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "practice_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"kc_id", "question_type"}

    kc_id: str
    count: int = 3
    difficulty: float = 0.5
    question_type: str = "solve"
    model: str = "claude-sonnet-4-6"


async def practice_workflow(
    config: PracticeConfig,
    input_data: Any,
    output_dir: Path,
    *,
    caller: Any = None,
    on_step: Any = None,
) -> dict:
    """Generate `config.count` practice questions for `config.kc_id`.

    Uses oprim.generate_variant with a KC-specific template question.
    Returns {status, items: [{question, kc_id, difficulty}], count}.
    """
    from oprim.generate_variant import VariantInput, generate_variant
    from obase.provider_registry import ProviderRegistry

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
        trail.record(event="start", kc_id=config.kc_id, count=config.count)

        template_q, _ = _get_template(config.kc_id)
        variant_type = _difficulty_to_variant_type(config.difficulty)

        items: list[dict] = []
        for i in range(config.count):
            inp = VariantInput(
                original_question=template_q,
                original_answer="",
                kc_ids=[config.kc_id],
                variant_type=variant_type,
                grade_level="高中",
                subject="math",
            )
            item = await generate_variant(inp, caller=caller, model=config.model)
            items.append({
                "question": item.question or template_q,
                "kc_id": config.kc_id,
                "difficulty": item.difficulty,
                "question_type": config.question_type,
                "kernel_verified": item.kernel_verified,
            })
            trail.record(event=f"item_{i + 1}", success=item.success)
            if on_step:
                on_step("practice_workflow", f"item_{i + 1}")

        fp = compute_fingerprint({"kc_id": config.kc_id, "question_type": config.question_type})
        trail_path = trail.write(output_dir)

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            cost_usd=cost.total_usd,
            items=items,
            count=len(items),
            kc_id=config.kc_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
            items=[],
            count=0,
            kc_id=config.kc_id,
        )


def _difficulty_to_variant_type(difficulty: float) -> str:
    if difficulty < 0.35:
        return "easier"
    if difficulty > 0.65:
        return "harder"
    return "same_structure"


class _MockCaller:
    async def __call__(self, **kwargs: Any) -> dict:
        return {
            "content": '{"question": "求函数 f(x) = x² + 2x - 3 的零点。", "answer": "", "difficulty": "medium", "kc_ids": []}',
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
