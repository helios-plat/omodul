"""omodul.learning_progress_report — Generate longitudinal learning progress report.

Composes oskill.longitudinal_pattern to analyze attempt history and
produce a structured progress report.

Pillars: fingerprint + decision_trail + report
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class LearningProgressConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "learning_progress_report"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"user_id", "period_key"}

    report_period_days: int = 30
    min_attempts_per_kc: int = 3


class ProgressInput(BaseModel):
    user_id: str
    period_key: str = ""
    attempt_records: list[dict] = []


async def learning_progress_report(
    config: LearningProgressConfig,
    input_data: ProgressInput,
    output_dir: Path,
    *,
    caller: Any = None,
    on_step: Any = None,
) -> dict:
    from oskill.longitudinal_pattern import AttemptRecord, longitudinal_pattern

    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        trail.record(event="start", user_id=input_data.user_id)

        records = [
            AttemptRecord(
                question_id=r.get("question_id", f"q{i}"),
                kc_id=r.get("kc_id", "unknown"),
                correct=bool(r.get("correct", False)),
                timestamp=float(r.get("timestamp", 0.0)),
                response_time_s=float(r.get("response_time_s", 0.0)),
            )
            for i, r in enumerate(input_data.attempt_records)
        ]

        pattern = longitudinal_pattern(records, min_attempts_per_kc=config.min_attempts_per_kc)
        trail.record(
            event="pattern_computed",
            kc_count=len(pattern.kc_trajectories),
            sessions=pattern.sessions_analyzed,
        )

        fp = compute_fingerprint({"user_id": input_data.user_id, "period_key": input_data.period_key})

        trend_dir = "improving" if pattern.overall_trend > 0.01 else (
            "declining" if pattern.overall_trend < -0.01 else "stable"
        )
        report = (
            f"# Learning Progress Report\n\n"
            f"User: {input_data.user_id}\n"
            f"Sessions analyzed: {pattern.sessions_analyzed}\n"
            f"Overall trend: {trend_dir} ({pattern.overall_trend:+.3f})\n\n"
            f"## KCs\n"
            f"- Improving: {', '.join(pattern.improving_kcs) or 'none'}\n"
            f"- Plateau: {', '.join(pattern.plateau_kcs) or 'none'}\n"
            f"- Forgetting: {', '.join(pattern.forgetting_kcs) or 'none'}\n"
        )
        report_path = output_dir / "progress_report.md"
        report_path.write_text(report, encoding="utf-8")

        trajectories = {
            kc_id: {
                "trend": t.trend,
                "is_plateau": t.is_plateau,
                "is_forgetting": t.is_forgetting,
                "peak_accuracy": t.peak_accuracy,
                "current_accuracy": t.current_accuracy,
                "attempt_count": t.attempt_count,
            }
            for kc_id, t in pattern.kc_trajectories.items()
        }

        trail_path = trail.write(output_dir)
        trail.record(event="done")

        if on_step:
            on_step("learning_progress_report", "done")

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            report_path=report_path,
            cost_usd=cost.total_usd,
            overall_trend=pattern.overall_trend,
            sessions_analyzed=pattern.sessions_analyzed,
            improving_kcs=pattern.improving_kcs,
            plateau_kcs=pattern.plateau_kcs,
            forgetting_kcs=pattern.forgetting_kcs,
            trajectories=trajectories,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
        )
