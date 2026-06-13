"""omodul.grade_paper_workflow — Grade a full problem set.

Composes oprim.grade_question for each question in the set.
Deterministic kernel grading takes priority over LLM (enforced in oprim layer).

Pillars: fingerprint + decision_trail + cost + report
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class GradePaperConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "grade_paper_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"paper_id", "user_id"}

    subject: str = "math"
    grade_level: int = 8
    model: str = "claude-sonnet-4-6"


class PaperQuestion(BaseModel):
    question_id: str = ""
    question: str
    student_answer: str
    expected_answer: str | None = None
    kc_ids: list[str] = []


class GradePaperInput(BaseModel):
    user_id: str = ""
    paper_id: str = ""
    questions: list[PaperQuestion] = []


async def grade_paper_workflow(
    config: GradePaperConfig,
    input_data: GradePaperInput,
    output_dir: Path,
    *,
    caller: Any,
    on_step: Any = None,
) -> dict:
    from oprim.grade_question import GradeQuestionInput, grade_question

    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        trail.record(event="start", paper_id=input_data.paper_id, n_questions=len(input_data.questions))

        grades: list[dict] = []
        correct_count = 0

        for i, q in enumerate(input_data.questions):
            inp = GradeQuestionInput(
                question=q.question,
                student_answer=q.student_answer,
                expected_answer=q.expected_answer,
                subject=config.subject,
                grade_level=config.grade_level,
            )
            result = await grade_question(inp, caller=caller, model=config.model)
            grades.append({
                "question_id": q.question_id or f"q{i+1}",
                "is_correct": result.is_correct,
                "method": result.method,
                "score": 1.0 if result.is_correct else 0.0,
            })
            if result.is_correct:
                correct_count += 1
            trail.record(event=f"graded_{i+1}", is_correct=result.is_correct, method=result.method)

            if on_step:
                on_step("grade_paper_workflow", f"q{i+1}")

        total = len(input_data.questions)
        score_pct = correct_count / max(total, 1) * 100

        fp = compute_fingerprint({"paper_id": input_data.paper_id, "user_id": input_data.user_id})

        report = (
            f"# Paper Grade Report\n\n"
            f"User: {input_data.user_id}  Paper: {input_data.paper_id}\n"
            f"Score: {correct_count}/{total} ({score_pct:.1f}%)\n\n"
        )
        for g in grades:
            mark = "V" if g["is_correct"] else "X"
            report += f"- {g['question_id']}: {mark} ({g['method']})\n"

        report_path = output_dir / "paper_grade.md"
        report_path.write_text(report, encoding="utf-8")

        trail_path = trail.write(output_dir)
        trail.record(event="done", score_pct=score_pct)

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            report_path=report_path,
            cost_usd=cost.total_usd,
            grades=grades,
            correct_count=correct_count,
            total_count=total,
            score_pct=score_pct,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
        )
