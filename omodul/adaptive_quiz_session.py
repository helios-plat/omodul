"""omodul.adaptive_quiz_session — Adaptive quiz generation workflow.

Composes oskill.generate_practice_set to produce an interleaved quiz
tailored to the student's current mastery.

Pillars: fingerprint + decision_trail
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class AdaptiveQuizConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "adaptive_quiz_session"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}
    _fingerprint_fields: ClassVar[set[str]] = {"user_id", "session_id"}

    target_count: int = 10
    mastery_threshold: float = 0.75
    max_difficulty: float = 1.0
    min_difficulty: float = 0.0
    balance_kcs: bool = True


class AdaptiveQuizInput(BaseModel):
    user_id: str
    session_id: str = ""
    question_bank: list[dict] = []
    kc_mastery: dict[str, float] = {}
    seed_kc_id: str | None = None


async def adaptive_quiz_session(
    config: AdaptiveQuizConfig,
    input_data: AdaptiveQuizInput,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict:
    from oskill.generate_practice_set import PracticeSetConfig, generate_practice_set
    from oskill.interleave_select import QuestionItem

    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        trail.record(event="start", user_id=input_data.user_id)

        bank = [
            QuestionItem(
                question_id=q.get("question_id", f"q{i}"),
                kc_id=q.get("kc_id", "unknown"),
                difficulty=float(q.get("difficulty", 0.5)),
                mastery=input_data.kc_mastery.get(
                    q.get("kc_id", ""), float(q.get("mastery", 0.5))
                ),
            )
            for i, q in enumerate(input_data.question_bank)
        ]

        cfg = PracticeSetConfig(
            target_count=config.target_count,
            mastery_threshold=config.mastery_threshold,
            max_difficulty=config.max_difficulty,
            min_difficulty=config.min_difficulty,
            balance_kcs=config.balance_kcs,
            seed_kc_id=input_data.seed_kc_id,
        )
        result = generate_practice_set(bank, kc_mastery=input_data.kc_mastery, config=cfg)

        trail.record(event="quiz_generated", count=len(result.questions))

        fp = compute_fingerprint({"user_id": input_data.user_id, "session_id": input_data.session_id})

        selected = [
            {"question_id": q.question_id, "kc_id": q.kc_id, "difficulty": q.difficulty}
            for q in result.questions
        ]

        trail_path = trail.write(output_dir)
        trail.record(event="done")

        if on_step:
            on_step("adaptive_quiz_session", "done")

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            cost_usd=cost.total_usd,
            questions=selected,
            dropped_count=result.dropped_count,
            kc_distribution=result.kc_distribution,
            mastery_coverage=result.mastery_coverage,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
        )
