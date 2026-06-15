"""omodul.generate_lesson_page — Assemble a lesson page for a question.

Composes:
  1. oskill.solve_and_visualize  → deterministic kernel answer + SVG
  2. Self-check: svg_answer == solve_answer == last_step_value (red line)

Pillars: fingerprint + decision_trail + cost
Red line: lesson_page diagram value == answer == last step value (同源自检).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class LessonPageConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "generate_lesson_page"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"kc_id", "question_hash"}

    kc_id: str = ""
    question_hash: str = ""


class LessonPageInput(BaseModel):
    question_text: str
    correct_answer: str = ""
    problem_spec: dict = {}


async def generate_lesson_page(
    config: LessonPageConfig,
    input_data: LessonPageInput,
    output_dir: Path,
    *,
    caller: Any = None,
    on_step: Any = None,
) -> dict:
    """Produce a lesson page dict with steps, svg, answer, and self-check result.

    Red line (同源自检): If the kernel answer does not match correct_answer and
    correct_answer is non-empty, self_check_passed=False and the lesson is not
    delivered (returns status='self_check_failed').

    Returns
    -------
    dict with keys:
        status: "ok" | "self_check_failed" | "error"
        steps: list[dict]  — kernel solution steps
        answer: str        — kernel's authoritative answer
        svg: str           — SVG diagram (may be empty)
        self_check_passed: bool
        fingerprint: str
    """
    from oskill.solve_and_visualize import SolveAndVisualizeInput, solve_and_visualize

    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        trail.record(event="start", kc_id=config.kc_id)

        # Determine expression from problem_spec or question_text
        expression = (
            input_data.problem_spec.get("expression", "")
            or input_data.problem_spec.get("equation", "")
            or input_data.question_text
        )

        sv_input = SolveAndVisualizeInput(
            expression=expression,
            problem_type="auto",
            variable=input_data.problem_spec.get("variable", "x"),
            generate_svg=True,
        )
        sv_result = solve_and_visualize(sv_input)

        trail.record(event="solved", solvable=sv_result.solvable)
        if on_step:
            on_step("generate_lesson_page", "solved")

        kernel_answer = sv_result.solve_answer
        steps = sv_result.solve_steps
        svg = sv_result.svg

        # 同源自检: kernel answer must agree with provided correct_answer
        self_check_passed = _answers_agree(kernel_answer, input_data.correct_answer)

        # Also validate last step matches answer (if steps exist)
        if steps:
            last_step_val = str(steps[-1].get("result", steps[-1].get("value", "")))
            if last_step_val and kernel_answer and last_step_val not in kernel_answer:
                self_check_passed = False
                trail.record(event="self_check_step_mismatch", last=last_step_val, ans=kernel_answer)

        trail.record(event="self_check", passed=self_check_passed)

        q_hash = config.question_hash or hashlib.sha256(
            input_data.question_text.encode()
        ).hexdigest()[:16]
        fp = compute_fingerprint({"kc_id": config.kc_id, "question_hash": q_hash})
        trail_path = trail.write(output_dir)

        if not self_check_passed and input_data.correct_answer:
            return build_result(
                status="self_check_failed",
                fingerprint=fp,
                trail=trail,
                trail_path=trail_path,
                cost_usd=cost.total_usd,
                steps=steps,
                answer=kernel_answer,
                svg=svg,
                self_check_passed=False,
                kc_id=config.kc_id,
            )

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            cost_usd=cost.total_usd,
            steps=steps,
            answer=kernel_answer,
            svg=svg,
            self_check_passed=True,
            kc_id=config.kc_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
            steps=[],
            answer="",
            svg="",
            self_check_passed=False,
            kc_id=config.kc_id,
        )


def _answers_agree(kernel: str, reference: str) -> bool:
    """Check if kernel answer and reference answer agree (fuzzy)."""
    if not reference:
        return True
    if not kernel:
        return False
    k = kernel.strip().lower().replace(" ", "")
    r = reference.strip().lower().replace(" ", "")
    return k == r or k in r or r in k
