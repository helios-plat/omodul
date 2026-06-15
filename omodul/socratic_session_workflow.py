"""omodul.socratic_session_workflow — Per-turn Socratic session orchestration.

Wraps oskill.socratic_loop for interactive per-turn use.
Supports on_step SSE streaming callback.

Red line: LLM must never reveal correct_answer (enforced by oskill layer).
Pillars: fingerprint + decision_trail + cost
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class SocraticConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "socratic_session_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"question_hash", "user_id"}

    mode: str = "mixed"       # "deep" | "mixed" | "sprint"
    max_turns: int = 20
    hint_level: int = 1
    model: str = "claude-sonnet-4-6"


class SocraticInput(BaseModel):
    question_text: str
    correct_answer: str
    kc_id: str = ""
    profiler_result: dict = {}
    student_messages: list[str] = []
    user_id: str = ""


async def socratic_session_workflow(
    config: SocraticConfig,
    input_data: SocraticInput,
    output_dir: Path,
    *,
    caller: Any = None,
    on_step: Any = None,
) -> dict:
    """Process accumulated student messages, return the next assistant turn.

    If student_messages is empty, returns the initial opening question.
    Otherwise processes the last student message and returns the assistant reply.

    Red line enforced at oskill layer: answer_leaked triggers substitution.
    """
    from obase.provider_registry import ProviderRegistry
    from oskill.socratic_loop import create_socratic_state, process_socratic_turn

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
        trail.record(event="start", user_id=input_data.user_id, kc_id=input_data.kc_id)

        state = create_socratic_state(input_data.question_text, input_data.correct_answer)
        turns: list[dict] = []

        # Replay prior messages to restore state, then process the latest
        messages_to_process = input_data.student_messages[: config.max_turns]
        if not messages_to_process:
            # Initial question — no student turn yet
            first_q = "请仔细审题，你认为这道题考察的是什么知识点？请先写出已知条件。"
            trail.record(event="initial_question")
            if on_step:
                on_step("socratic_session_workflow", "initial_question")
            fp = compute_fingerprint({"question_hash": str(hash(input_data.question_text))[:12],
                                      "user_id": input_data.user_id})
            return build_result(
                status="ok",
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(output_dir),
                cost_usd=cost.total_usd,
                first_question=first_q,
                turns=[],
                turn_count=0,
                resolved=False,
                violation_count=0,
            )

        for i, msg in enumerate(messages_to_process):
            out = await process_socratic_turn(
                state,
                msg,
                caller=caller,
                kc_ids=[input_data.kc_id] if input_data.kc_id else [],
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
            trail.record(event=f"turn_{i + 1}", leaked=out.answer_leaked)
            if on_step:
                on_step("socratic_session_workflow", f"turn_{i + 1}::{out.assistant_text}")

        fp = compute_fingerprint({"question_hash": str(hash(input_data.question_text))[:12],
                                  "user_id": input_data.user_id})
        latest_reply = turns[-1]["assistant"] if turns else ""
        return build_result(
            status="ok",
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(output_dir),
            cost_usd=cost.total_usd,
            first_question=latest_reply,
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
            first_question="这道题请你再仔细想想。",
            turns=[],
        )


class _MockCaller:
    async def __call__(self, **kwargs: Any) -> dict:
        return {"content": "请继续思考，这一步你是怎么想的？", "usage": {"input_tokens": 0, "output_tokens": 0}}
