"""
omodul.share_session — Redact and share a session via an uploader or stub URL.

Pillars: fingerprint
"""
from __future__ import annotations

import asyncio
import copy
import inspect
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
    """Fingerprint over session_id."""
    return compute_fingerprint({"session_id": input_data.session_id})


def _redact(data: Any, keys: list[str]) -> Any:
    """Recursively mask sensitive keys in a dict/list structure."""
    if isinstance(data, dict):
        return {
            k: ("***REDACTED***" if k in keys else _redact(v, keys))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redact(item, keys) for item in data]
    return data


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "share_session"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"session_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}

    redact_keys: list[str] = ["api_key", "token", "password", "secret"]


class InputData(BaseModel):
    session_id: str
    session_data: dict | None = None
    uploader: Any = None
    loader: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def share_session(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Redact sensitive fields from a session and publish it, returning a share URL."""
    try:
        fp = compute_fingerprint_for(config, input_data)

        if input_data.session_data is not None:
            data = input_data.session_data
        elif input_data.loader is not None:
            data = await _call(input_data.loader, session_id=input_data.session_id)
        else:
            data = {}

        redacted = _redact(copy.deepcopy(data), config.redact_keys)

        if input_data.uploader is not None:
            url = await _call(input_data.uploader, payload=redacted)
        else:
            url = f"https://share.example.com/{input_data.session_id}"

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            share_url=url,
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
