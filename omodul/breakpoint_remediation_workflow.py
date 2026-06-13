"""omodul.breakpoint_remediation_workflow — Analyze mistakes and produce remediation plan.

Composes oprim.find_common_breakpoint to identify conceptual gaps from
wrong answers, then builds a structured remediation plan.

Pillars: fingerprint + decision_trail + cost + report
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class BreakpointRemediationConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "breakpoint_remediation_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"user_id", "session_id"}

    max_remediation_items: int = 5
    priority_kcs: list[str] = []
    model: str = "claude-sonnet-4-6"


class WrongQuestionEntry(BaseModel):
    question_id: str = ""
    question_text: str = ""
    student_answer: str = ""
    correct_answer: str = ""
    kc_ids: list[str] = []
    error_type: str = ""


class BreakpointRemediationInput(BaseModel):
    user_id: str = ""
    session_id: str = ""
    wrong_questions: list[WrongQuestionEntry] = []


async def breakpoint_remediation_workflow(
    config: BreakpointRemediationConfig,
    input_data: BreakpointRemediationInput,
    output_dir: Path,
    *,
    caller: Any,
    on_step: Any = None,
) -> dict:
    from oprim.find_common_breakpoint import WrongQuestion, find_common_breakpoint

    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        trail.record(event="start", user_id=input_data.user_id, n_wrong=len(input_data.wrong_questions))

        if not input_data.wrong_questions:
            trail.record(event="no_wrong_questions")
            return build_result(
                status="ok",
                cost_usd=0.0,
                breakpoints=[],
                dominant_error_type="",
                remediation_plan="No wrong questions to analyze.",
            )

        wq_list = [
            WrongQuestion(
                question_id=q.question_id,
                question_text=q.question_text,
                student_answer=q.student_answer,
                correct_answer=q.correct_answer,
                kc_ids=q.kc_ids,
                error_type=q.error_type,
            )
            for q in input_data.wrong_questions
        ]

        result = await find_common_breakpoint(wq_list, caller=caller, model=config.model)
        trail.record(event="breakpoints_found", count=len(result.breakpoints))

        fp = compute_fingerprint({"user_id": input_data.user_id, "session_id": input_data.session_id})

        remediation_lines = [f"# Remediation Plan\n\nUser: {input_data.user_id}\n"]
        remediation_lines.append(f"Dominant error: {result.dominant_error_type}\n\n## Breakpoints\n")
        for bp in result.breakpoints[:config.max_remediation_items]:
            remediation_lines.append(f"- {bp}\n")
        remediation_lines.append(f"\n## Summary\n{result.summary}\n")
        remediation_text = "".join(remediation_lines)

        report_path = output_dir / "remediation_plan.md"
        report_path.write_text(remediation_text, encoding="utf-8")

        trail_path = trail.write(output_dir)
        trail.record(event="done")

        if on_step:
            on_step("breakpoint_remediation_workflow", "done")

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            report_path=report_path,
            cost_usd=cost.total_usd,
            breakpoints=result.breakpoints,
            dominant_error_type=result.dominant_error_type,
            affected_question_ids=result.affected_question_ids,
            remediation_plan=remediation_text,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
        )
