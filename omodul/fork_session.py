"""
omodul.fork_session — Fork an existing session, optionally from a history slice.

Pillars: fingerprint
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, build_result, compute_fingerprint


async def _call(fn: Any, **kwargs: Any) -> Any:
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def compute_fingerprint_for(config: "Config", input_data: "InputData") -> str:
    """Fingerprint over source_session_id."""
    return compute_fingerprint({"source_session_id": input_data.source_session_id})


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "fork_session"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"source_session_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class InputData(BaseModel):
    source_session_id: str
    history_slice: list[dict] | None = None
    loader: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def fork_session(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Fork a session by copying or slicing its history into a new session ID."""
    try:
        fp = compute_fingerprint_for(config, input_data)

        if input_data.loader is not None:
            source = await _call(input_data.loader,
                                 session_id=input_data.source_session_id)
        else:
            source = {
                "id": input_data.source_session_id,
                "history": input_data.history_slice or [],
            }

        new_id = str(uuid.uuid4())
        history = (
            input_data.history_slice
            if input_data.history_slice is not None
            else source.get("history", [])
        )

        forked = {
            **source,
            "id": new_id,
            "parent_id": input_data.source_session_id,
            "history": history,
        }

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            session_id=new_id,
            forked_session=forked,
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
