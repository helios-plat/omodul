"""
omodul.create_session — Create a new agent session with a unique ID.

Pillars: fingerprint
Sync omodul.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, build_result, compute_fingerprint


def compute_fingerprint_for(config: "Config", input_data: "InputData") -> str:
    """Fingerprint over initial_model + agent_type."""
    return compute_fingerprint({
        "initial_model": input_data.initial_model or config.initial_model,
        "agent_type": input_data.agent_type or config.agent_type,
    })


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "create_session"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"initial_model", "agent_type"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}

    initial_model: str = "claude-sonnet-4-6"
    agent_type: str = "build"


class InputData(BaseModel):
    title: str = ""
    initial_model: str = ""
    agent_type: str = ""


def create_session(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Create a new session record with metadata."""
    try:
        fp = compute_fingerprint_for(config, input_data)
        session_id = str(uuid.uuid4())
        resolved_model = input_data.initial_model or config.initial_model
        resolved_type = input_data.agent_type or config.agent_type

        session = {
            "id": session_id,
            "title": input_data.title or f"Session {session_id[:8]}",
            "model": resolved_model,
            "agent_type": resolved_type,
            "history": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            session_id=session_id,
            session=session,
        )

    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
