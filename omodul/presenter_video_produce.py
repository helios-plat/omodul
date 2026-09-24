"""Standard transaction for a presenter or scene-based rendered video.

Applications inject their renderer so this reusable operation stays free of
product state, databases, and vendor-specific presentation logic.  Its shared
responsibility is to normalize failure handling and refuse a successful result
unless the declared final video exists.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

_enabled_pillars = {"fingerprint", "decision_trail", "report", "cost"}
_CONFIG_FIELDS = {"renderer", "format", "schema_version"}
Renderer = Callable[[dict[str, Any], Path, dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


def compute_fingerprint_for(config: dict[str, Any], input_data: dict[str, Any]) -> str:
    """Identify the executable contract without retaining presentation content."""

    payload = {
        "operation": "presenter_video_produce",
        "config": {key: config.get(key) for key in sorted(_CONFIG_FIELDS) if key in config},
        "input_schema": input_data.get("schema_version", 1),
        "presentation_kind": (
            input_data.get("presentation", {}).get("kind")
            if isinstance(input_data.get("presentation"), dict)
            else None
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _failed(*, code: str, message: str, fingerprint: str, stage: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "error": {"code": code, "message": message[:500]},
        "artifacts": [],
        "fingerprint": fingerprint,
        "decision_trail": [{"stage": stage, "outcome": "failed"}],
        "cost": {},
        "report": {},
    }


async def presenter_video_produce(
    config: dict[str, Any], input_data: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Run an injected presenter renderer and normalize its verified result."""

    fingerprint = compute_fingerprint_for(config, input_data)
    presentation = input_data.get("presentation")
    renderer = input_data.get("renderer")
    if not isinstance(presentation, dict):
        return _failed(
            code="INVALID_INPUT",
            message="input_data.presentation is required",
            fingerprint=fingerprint,
            stage="validation",
        )
    if not callable(renderer):
        return _failed(
            code="MISSING_INJECTION",
            message="input_data.renderer must be an injected renderer callable",
            fingerprint=fingerprint,
            stage="validation",
        )

    try:
        rendered = renderer(presentation, Path(output_dir), config)
        if inspect.isawaitable(rendered):
            rendered = await rendered
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return _failed(
            code=type(exc).__name__.upper(),
            message=str(exc),
            fingerprint=fingerprint,
            stage="render",
        )
    if not isinstance(rendered, dict):
        return _failed(
            code="INVALID_RENDERER_RESULT",
            message="renderer must return a mapping containing video_path",
            fingerprint=fingerprint,
            stage="artifact_validation",
        )

    video = rendered.get("video_path")
    video_path = Path(video) if isinstance(video, str | Path) else None
    if video_path is None or not video_path.is_file():
        return _failed(
            code="ARTIFACT_MISSING",
            message=f"presenter video was not created: {video}",
            fingerprint=fingerprint,
            stage="artifact_validation",
        )

    report = rendered.get("report")
    return {
        "status": "succeeded",
        "error": None,
        "artifacts": [
            {"kind": "video", "path": str(video_path), "media_type": "video/mp4", "primary": True}
        ],
        "fingerprint": fingerprint,
        "decision_trail": [
            {"stage": "render", "outcome": "completed"},
            {"stage": "artifact_validation", "outcome": "passed"},
        ],
        "cost": {},
        "report": report if isinstance(report, dict) else {},
    }


__manifest__ = {
    "name": "presenter_video_produce",
    "kind": "omodul",
    "signature": "(config, input_data, output_dir) -> dict",
    "depends_on": ["injected_renderer"],
    "pillars": sorted(_enabled_pillars),
}
