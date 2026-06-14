"""
omodul.compact_session — Compact a session history when it exceeds the token threshold.

Pillars: fingerprint, decision_trail
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


async def _call(fn: Any, **kwargs: Any) -> Any:
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def compute_fingerprint_for(config: "Config", input_data: "InputData") -> str:
    """Fingerprint over session_id + history length."""
    return compute_fingerprint({
        "session_id": input_data.session_id,
        "history_len": len(input_data.history),
    })


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "compact_session"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"session_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    token_threshold: int = 100_000


class InputData(BaseModel):
    session_id: str
    history: list[dict] = []
    compactor: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def compact_session(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Compact session history if it exceeds the configured token threshold."""
    trail = Trail()
    fp = compute_fingerprint_for(config, input_data)

    try:
        trail.record(event="check_should_compact", step_no=0,
                     session_id=input_data.session_id,
                     history_len=len(input_data.history),
                     threshold=config.token_threshold)

        should_compact = (
            len(input_data.history) > config.token_threshold
            or input_data.compactor is not None
        )

        if not should_compact:
            return build_result(
                status="completed",
                error=None,
                trail=trail,
                fingerprint=fp,
                compacted=False,
            )

        new_history = await _call(input_data.compactor, history=input_data.history)
        if new_history is None:
            new_history = input_data.history

        trail.record(event="compact_done", step_no=1,
                     new_history_len=len(new_history))

        loop = asyncio.get_event_loop()
        await asyncio.shield(loop.run_in_executor(None, trail.write, output_dir))

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            fingerprint=fp,
            compacted=True,
            new_history_len=len(new_history),
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            fingerprint=fp,
        )
