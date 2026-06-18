"""
omodul.essay_review_workflow — Rubric-scored essay review with guidance questions.

Pillars: fingerprint, report, decision_trail
Composes: oskill.essay_assessment
Report content: rubric scores + guidance questions (NO model essay).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import (
    BaseConfig, CostTracker, Trail, build_result, compute_fingerprint,
    write_report,
)


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "essay_review_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "report", "decision_trail"}


class InputData(BaseModel):
    essay_text: str
    grade_level: str = "高中"
    essay_type: str = "议论文"
    user_id: str = ""
    llm_caller: Any = None
    rubric: dict | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


def compute_fingerprint_for_essay_review_workflow(essay_text: str, grade_level: str, essay_type: str) -> str:
    """Compute deterministic fingerprint for an essay review request."""
    return compute_fingerprint({
        "essay_text": essay_text,
        "grade_level": grade_level,
        "essay_type": essay_type,
    })


async def essay_review_workflow(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Run a full essay review: rubric scoring + LLM guidance questions + report.

    Returns a build_result dict with fingerprint, report_path, decision_trail, cost_usd.
    Report contains rubric scores and guidance questions only — no model essay.
    """
    trail = Trail()
    cost = CostTracker()

    fingerprint = compute_fingerprint_for_essay_review_workflow(
        input_data.essay_text, input_data.grade_level, input_data.essay_type
    )

    try:
        from oprim._mneme_speech_types import EssayAssessmentInput
        from oskill._essay_assessment import essay_assessment

        trail.record(event="review_start", fingerprint=fingerprint, grade_level=input_data.grade_level)

        inp = EssayAssessmentInput(
            essay_text=input_data.essay_text,
            grade_level=input_data.grade_level,
            essay_type=input_data.essay_type,
            user_id=input_data.user_id,
        )

        result = await essay_assessment(
            inp,
            llm=input_data.llm_caller,
            rubric=input_data.rubric,
            model=config.llm_model,
        )

        trail.record(
            event="assessment_complete",
            revision_needed=result.revision_needed,
            n_questions=len(result.guidance_questions),
        )

        # Build report: rubric scores + guidance questions, NO model essay
        report_content = _build_report(result, input_data)
        report_path = write_report(
            report_content,
            output_dir=output_dir,
            name=f"essay_review_{fingerprint[:8]}",
            fmt="markdown",
        )

        trail.record(event="report_written", path=str(report_path))
        trail_path = trail.write(output_dir)

        return build_result(
            status="completed",
            error=None,
            fingerprint=fingerprint,
            trail=trail,
            trail_path=trail_path,
            report_path=str(report_path),
            cost_usd=cost.total_usd,
            revision_needed=result.revision_needed,
            rubric_scores=result.rubric_scores,
            guidance_questions=result.guidance_questions,
        )

    except asyncio.CancelledError:
        trail.record(event="cancelled")
        trail.write(output_dir)
        raise

    except Exception as exc:
        trail.record(event="error", error_type=type(exc).__name__, message=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fingerprint,
            trail=trail,
            trail_path=None,
            cost_usd=cost.total_usd,
        )


def _build_report(result, input_data: InputData) -> str:
    lines = [
        f"# 作文评测报告",
        f"",
        f"**年级**：{input_data.grade_level}  **文体**：{input_data.essay_type}",
        f"**是否需要修改**：{'是' if result.revision_needed else '否'}",
        f"",
        "## 各维度得分",
        "",
    ]
    for dim, score in result.rubric_scores.items():
        lines.append(f"- **{dim}**：{score:.1f} 分")
    lines += [
        "",
        "## 引导性问题",
        "",
        "以下问题帮助你自己发现需要改进的地方。请认真思考，不要参考范文。",
        "",
    ]
    for i, q in enumerate(result.guidance_questions, 1):
        lines.append(f"{i}. {q}")
    return "\n".join(lines)
