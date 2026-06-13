"""omodul.socratic_tutor_session — Orchestrate a full Socratic tutoring session.

Wraps oskill.socratic_loop with omodul infrastructure.
Red line: LLM must never reveal the correct answer (enforced in oskill layer).

Pillars: fingerprint + decision_trail + cost
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class SocraticTutorConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "socratic_tutor_session"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"question_hash", "user_id"}

    max_turns: int = 8
    hint_level: int = 1
    model: str = "claude-sonnet-4-6"


class SocraticTutorInput(BaseModel):
    user_id: str = ""
    question: str
    correct_answer: str
    kc_ids: list[str] = []
    student_messages: list[str] = []


async def socratic_tutor_session(
    config: SocraticTutorConfig,
    input_data: SocraticTutorInput,
    output_dir: Path,
    *,
    caller: Any,
    on_step: Any = None,
) -> dict:
    from oskill.socratic_loop import create_socratic_state, process_socratic_turn

    cost = CostTracker()
    trail = Trail()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        trail.record(event="start", user_id=input_data.user_id)

        state = create_socratic_state(input_data.question, input_data.correct_answer)
        turns: list[dict] = []

        for i, msg in enumerate(input_data.student_messages[:config.max_turns]):
            out = await process_socratic_turn(
                state,
                msg,
                caller=caller,
                kc_ids=input_data.kc_ids,
                model=config.model,
                hint_level=config.hint_level,
            )
            turns.append({
                "turn": out.turn_number,
                "student": msg,
                "assistant": out.assistant_text,
                "step_check": out.step_check_triggered,
                "answer_leaked": out.answer_leaked,
            })
            trail.record(event=f"turn_{i+1}", leaked=out.answer_leaked)

            if on_step:
                on_step("socratic_tutor_session", f"turn_{i+1}")

        q_hash = str(hash(input_data.question))[:12]
        fp = compute_fingerprint({"question_hash": q_hash, "user_id": input_data.user_id})
        trail_path = trail.write(output_dir)
        trail.record(event="done", turns=len(turns))

        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            cost_usd=cost.total_usd,
            turns=turns,
            turn_count=state.turn_count,
            resolved=state.resolved,
            violation_count=state.violation_count,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="error",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
        )
