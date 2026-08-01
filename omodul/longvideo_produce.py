"""Standard 3O transaction wrapper for the legacy long-video pipeline.

``agentic_longvideo_pipeline`` remains the compatibility implementation.  This
module is the stable public boundary used by service engines: it never raises
ordinary provider/validation failures and always returns a structured result.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

from omodul.agentic_longvideo_pipeline import LongVideoConfig, agentic_longvideo_pipeline

_enabled_pillars = {"fingerprint", "decision_trail", "report", "cost"}
_CONFIG_FIELDS = {
    "duration_archetype",
    "video_provider",
    "audio_provider",
    "style",
    "num_characters",
    "language",
    "max_shot_retries",
    "consistency_threshold",
    "fallback_video_provider",
    "max_concurrent_shots",
    "target_duration_s",
}


def _failed(
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


async def _run_injected_renderer(
    renderer: Any, *, topic: str, output_dir: Path, config: dict[str, Any], fingerprint: str
) -> dict[str, Any]:
    """Normalize an application renderer without importing application code."""

    try:
        rendered = renderer(topic, output_dir, config)
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

    metadata = rendered.get("metadata") if isinstance(rendered.get("metadata"), dict) else {}
    shots = rendered.get("shots") if isinstance(rendered.get("shots"), list) else []
    return {
        "status": "succeeded",
        "error": None,
        "artifacts": [
            {"kind": "video", "path": str(video_path), "media_type": "video/mp4", "primary": True}
        ],
        "fingerprint": fingerprint,
        "decision_trail": [
            {"stage": "render", "outcome": "completed", "renderer": "injected"},
            {"stage": "artifact_validation", "outcome": "passed"},
        ],
        "cost": {},
        "report": {
            "video_path": str(video_path),
            "duration_s": rendered.get("duration"),
            "shots_generated": metadata.get("shots", len(shots)),
            "shots": shots,
            "quality": rendered.get("quality"),
        },
    }


def compute_fingerprint_for(config: dict[str, Any], input_data: dict[str, Any]) -> str:
    """Return a reproducible, PII-free execution fingerprint.

    Deliberately excludes the topic and any provider injection: HEVI owns user
    content and secrets, while the reusable transaction fingerprints only its
    executable configuration and input schema version.
    """

    payload = {
        "operation": "longvideo_produce",
        "config": {key: config.get(key) for key in sorted(_CONFIG_FIELDS) if key in config},
        "input_schema": input_data.get("schema_version", 1),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


async def longvideo_produce(
    config: dict[str, Any], input_data: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Produce a long video through the standard omodul transaction contract."""

    topic = input_data.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        return {
            "status": "failed",
            "error": {"code": "INVALID_INPUT", "message": "input_data.topic is required"},
            "artifacts": [],
            "fingerprint": compute_fingerprint_for(config, input_data),
            "decision_trail": [{"stage": "validation", "outcome": "rejected"}],
            "cost": {},
            "report": {},
        }

    cfg_values = {key: config[key] for key in _CONFIG_FIELDS if key in config}
    cfg_values.update({"topic": topic, "output_dir": Path(output_dir)})
    fingerprint = compute_fingerprint_for(config, input_data)
    renderer = input_data.get("renderer")
    if renderer is not None:
        if not callable(renderer):
            return _failed(
                code="MISSING_INJECTION",
                message="input_data.renderer must be an injected renderer callable",
                fingerprint=fingerprint,
                stage="validation",
            )
        return await _run_injected_renderer(
            renderer,
            topic=topic,
            output_dir=Path(output_dir),
            config=config,
            fingerprint=fingerprint,
        )
    try:
        result = await agentic_longvideo_pipeline(
            config=LongVideoConfig(**cfg_values),
            _providers=input_data.get("providers"),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return {
            "status": "failed",
            "error": {"code": type(exc).__name__.upper(), "message": str(exc)[:500]},
            "artifacts": [],
            "fingerprint": fingerprint,
            "decision_trail": [{"stage": "pipeline", "outcome": "failed"}],
            "cost": {},
            "report": {},
        }

    video_path = Path(result.video_path)
    if not video_path.is_file():
        return {
            "status": "failed",
            "error": {"code": "ARTIFACT_MISSING", "message": f"video was not created: {video_path}"},
            "artifacts": [],
            "fingerprint": fingerprint,
            "decision_trail": [{"stage": "artifact_validation", "outcome": "failed"}],
            "cost": {},
            "report": {"shots_generated": result.shots_generated},
        }

    shots = [shot.model_dump(mode="json") for shot in result.shots]
    return {
        "status": "succeeded",
        "error": None,
        "artifacts": [{"kind": "video", "path": str(video_path), "media_type": "video/mp4", "primary": True}],
        "fingerprint": fingerprint,
        "decision_trail": [
            {"stage": "pipeline", "outcome": "completed", "shots_generated": result.shots_generated},
            {"stage": "artifact_validation", "outcome": "passed"},
        ],
        "cost": {},
        "report": {
            "duration_s": result.duration_s,
            "shots_generated": result.shots_generated,
            "failed_shots": result.failed_shots,
            "provider_used": result.provider_used,
            "shots": shots,
        },
    }


__manifest__ = {
    "name": "longvideo_produce",
    "kind": "omodul",
    "signature": "(config, input_data, output_dir) -> dict",
    "depends_on": ["agentic_longvideo_pipeline"],
    "pillars": sorted(_enabled_pillars),
}
