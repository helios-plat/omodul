"""
omodul.speaking_practice_workflow — Guided English speaking practice session.

Pillars: decision_trail, cost
Composes: oskill.english_speaking_practice
Persistence: obase.persistence.insert_one → speaking_sessions table
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import (
    BaseConfig, CostTracker, Trail, build_result,
)


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "speaking_practice_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}

    max_turns: int = 5


class InputData(BaseModel):
    topic: str
    user_id: str = ""
    tts: Any = None
    stt: Any = None
    pronunciation_eval: Any = None
    llm_caller: Any = None
    db_pool: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def speaking_practice_workflow(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Run a full speaking practice session and persist the result.

    Returns a build_result dict with decision_trail and cost_usd.
    """
    trail = Trail()
    cost = CostTracker()
    session_id = uuid.uuid4().hex[:16]

    try:
        from oskill._english_speaking_practice import english_speaking_practice

        trail.record(event="session_start", session_id=session_id, topic=input_data.topic)

        practice_result = await english_speaking_practice(
            topic=input_data.topic,
            max_turns=config.max_turns,
            tts=input_data.tts,
            stt=input_data.stt,
            pronunciation_eval=input_data.pronunciation_eval,
            llm=input_data.llm_caller,
            model=config.llm_model,
        )

        trail.record(
            event="session_complete",
            turns=len(practice_result.turns),
            overall_progress=practice_result.overall_progress,
        )

        # Persist to speaking_sessions
        from dataclasses import asdict
        import json
        pron_scores_dicts = [asdict(p) for p in practice_result.pronunciation_scores]

        if input_data.db_pool is not None:
            from obase.persistence import insert_one
            await insert_one(
                input_data.db_pool,
                table="speaking_sessions",
                data={
                    "id": session_id,
                    "student_id": input_data.user_id,
                    "topic": input_data.topic,
                    "turns": json.dumps(practice_result.turns) if isinstance(practice_result.turns, (list, dict)) else practice_result.turns,
                    "pronunciation_scores": json.dumps(pron_scores_dicts) if isinstance(pron_scores_dicts, (list, dict)) else pron_scores_dicts,
                    "overall_progress": practice_result.overall_progress,
                },
            )
            trail.record(event="persisted", table="speaking_sessions")

        trail_path = asyncio.shield(
            asyncio.get_event_loop().run_in_executor(None, trail.write, output_dir)
        )
        try:
            tp = trail.write(output_dir)
        except Exception:
            tp = None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=tp,
            cost_usd=cost.total_usd,
            session_id=session_id,
            overall_progress=practice_result.overall_progress,
            turns=practice_result.turns,
            pronunciation_scores=pron_scores_dicts,
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
            trail=trail,
            trail_path=None,
            cost_usd=cost.total_usd,
        )
