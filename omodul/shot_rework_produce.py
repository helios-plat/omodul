"""Standard transaction for targeted long-video shot rework.

The operation owns the portable transaction contract; applications inject their
existing renderer so product-specific prompts, identity references and review
policy never leak into the reusable layer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

_enabled_pillars = {"fingerprint", "decision_trail", "report", "cost"}
_CONFIG_FIELDS = {
    "video_provider",
    "audio_provider",
    "style",
    "max_shot_retries",
    "consistency_threshold",
}


def compute_fingerprint_for(config: dict[str, Any], input_data: dict[str, Any]) -> str:
    """Fingerprint only executable configuration and anonymous request shape."""

    payload = {
        "operation": "shot_rework_produce",
        "config": {key: config.get(key) for key in sorted(_CONFIG_FIELDS) if key in config},
        "input_schema": input_data.get("schema_version", 1),
        "target_count": len(input_data.get("shot_ids", [])),
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


async def shot_rework_produce(
    config: dict[str, Any], input_data: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Rework selected shots and require a real reassembled video artifact.

    ``input_data.renderer`` is an application-owned callable with the signature
    ``(output_dir, config, shot_ids, hints) -> dict``.  It may be async and must
    return ``url`` or ``video_path`` plus an optional full ``shots`` list.
    """

    fingerprint = compute_fingerprint_for(config, input_data)
    shot_ids = input_data.get("shot_ids")
    if (
        not isinstance(shot_ids, list)
        or not shot_ids
        or any(isinstance(shot_id, bool) or not isinstance(shot_id, int) for shot_id in shot_ids)
    ):
        return _failed(
            code="INVALID_INPUT",
            message="input_data.shot_ids must be a non-empty list of integer indexes",
            fingerprint=fingerprint,
            stage="validation",
        )
    renderer = input_data.get("renderer")
    if not callable(renderer):
        return _failed(
            code="MISSING_INJECTION",
            message="input_data.renderer must be an injected shot rework callable",
            fingerprint=fingerprint,
            stage="validation",
        )

    hints = input_data.get("hints")
    if hints is not None and not isinstance(hints, dict):
        return _failed(
            code="INVALID_INPUT",
            message="input_data.hints must be a mapping when supplied",
            fingerprint=fingerprint,
            stage="validation",
        )
    try:
        rendered = renderer(Path(output_dir), config, shot_ids, hints or {})
        if hasattr(rendered, "__await__"):
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
            message="renderer must return a mapping containing video_path or url",
            fingerprint=fingerprint,
            stage="artifact_validation",
        )
    video = rendered.get("video_path") or rendered.get("url")
    video_path = Path(video) if isinstance(video, str | Path) else None
    if video_path is None or not video_path.is_file():
        return _failed(
            code="ARTIFACT_MISSING",
            message=f"video was not created: {video}",
            fingerprint=fingerprint,
            stage="artifact_validation",
        )

    shots = rendered.get("shots") if isinstance(rendered.get("shots"), list) else []
    return {
        "status": "succeeded",
        "error": None,
        "artifacts": [
            {"kind": "video", "path": str(video_path), "media_type": "video/mp4", "primary": True}
        ],
        "fingerprint": fingerprint,
        "decision_trail": [
            {"stage": "render", "outcome": "completed", "targets": len(shot_ids)},
            {"stage": "artifact_validation", "outcome": "passed"},
        ],
        "cost": {},
        "report": {
            "video_path": str(video_path),
            "shots": shots,
            "reworked_shot_count": len(shot_ids),
        },
    }


__manifest__ = {
    "name": "shot_rework_produce",
    "kind": "omodul",
    "signature": "(config, input_data, output_dir) -> dict",
    "depends_on": ["injected_renderer"],
    "pillars": sorted(_enabled_pillars),
}
