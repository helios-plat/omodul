"""Standard transaction for a narrated, renderer-backed video.

The operation intentionally receives the concrete renderer through injection.
That keeps this reusable 3O layer independent of any application UI, task
table, or render implementation (for example HEVI's Remotion renderer), while
still making artifact validation and error reporting part of one public
transaction contract.
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
_CONFIG_FIELDS = {"renderer", "voice", "rate", "format", "schema_version"}
Renderer = Callable[[dict[str, Any], Path, dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


def compute_fingerprint_for(config: dict[str, Any], input_data: dict[str, Any]) -> str:
    """Return a PII-free identity of the narrated production contract.

    Storyboard narration and titles are user-provided content, so only their
    schema and segment count participate in the fingerprint.
    """

    storyboard = input_data.get("storyboard")
    segment_count = len(storyboard.get("segments", [])) if isinstance(storyboard, dict) else None
    payload = {
        "operation": "narrated_video_produce",
        "config": {key: config.get(key) for key in sorted(_CONFIG_FIELDS) if key in config},
        "input_schema": input_data.get("schema_version", 1),
        "segment_count": segment_count,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _failure(
    *, code: str, message: str, fingerprint: str, stage: str, report: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "status": "failed",
        "error": {"code": code, "message": message[:500]},
        "artifacts": [],
        "fingerprint": fingerprint,
        "decision_trail": [{"stage": stage, "outcome": "failed"}],
        "cost": {},
        "report": report or {},
    }


async def narrated_video_produce(
    config: dict[str, Any], input_data: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Run an injected narrated renderer and return only verified artifacts."""

    fingerprint = compute_fingerprint_for(config, input_data)
    storyboard = input_data.get("storyboard")
    renderer = input_data.get("renderer")
    if not isinstance(storyboard, dict) or not isinstance(storyboard.get("segments"), list):
        return _failure(
            code="INVALID_INPUT",
            message="input_data.storyboard with segments is required",
            fingerprint=fingerprint,
            stage="validation",
        )
    if not callable(renderer):
        return _failure(
            code="MISSING_INJECTION",
            message="input_data.renderer must be an injected renderer callable",
            fingerprint=fingerprint,
            stage="validation",
        )

    try:
        rendered = renderer(storyboard, Path(output_dir), config)
        if inspect.isawaitable(rendered):
            rendered = await rendered
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return _failure(
            code=type(exc).__name__.upper(),
            message=str(exc),
            fingerprint=fingerprint,
            stage="render",
        )
    if not isinstance(rendered, dict):
        return _failure(
            code="INVALID_RENDERER_RESULT",
            message="renderer must return a mapping containing portrait_path",
            fingerprint=fingerprint,
            stage="artifact_validation",
        )

    portrait = rendered.get("portrait_path") or rendered.get("video_path")
    portrait_path = Path(portrait) if isinstance(portrait, str | Path) else None
    if portrait_path is None or not portrait_path.is_file():
        return _failure(
            code="ARTIFACT_MISSING",
            message=f"portrait video was not created: {portrait}",
            fingerprint=fingerprint,
            stage="artifact_validation",
        )

    artifacts: list[dict[str, Any]] = [
        {"kind": "video", "path": str(portrait_path), "media_type": "video/mp4", "primary": True}
    ]
    landscape = rendered.get("landscape_path")
    if landscape is not None:
        landscape_path = Path(landscape) if isinstance(landscape, str | Path) else None
        if landscape_path is None or not landscape_path.is_file():
            return _failure(
                code="ARTIFACT_MISSING",
                message=f"landscape video was not created: {landscape}",
                fingerprint=fingerprint,
                stage="artifact_validation",
            )
        artifacts.append(
            {"kind": "video", "path": str(landscape_path), "media_type": "video/mp4", "primary": False}
        )

    return {
        "status": "succeeded",
        "error": None,
        "artifacts": artifacts,
        "fingerprint": fingerprint,
        "decision_trail": [
            {"stage": "render", "outcome": "completed"},
            {"stage": "artifact_validation", "outcome": "passed"},
        ],
        "cost": {},
        "report": {
            "segment_count": len(storyboard["segments"]),
            "renderer": str(config.get("renderer", "injected")),
        },
    }


__manifest__ = {
    "name": "narrated_video_produce",
    "kind": "omodul",
    "signature": "(config, input_data, output_dir) -> dict",
    "depends_on": ["injected_renderer"],
    "pillars": sorted(_enabled_pillars),
}
