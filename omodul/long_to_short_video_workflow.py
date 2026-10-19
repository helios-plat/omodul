"""omodul.long_to_short_video_workflow — long video -> short clips."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel


class LongToShortVideoConfig(BaseModel):
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    output_format: str = "markdown"
    budget_usd: float = 1.0
    overwrite: bool = True
    top_k: int = 5
    portrait_width: int = 1080
    portrait_height: int = 1920

    _omodul_name: str = "long_to_short_video_workflow"
    _omodul_version: str = "0.1.0"
    _fingerprint_fields: set = set()
    _enabled_pillars: set = {"fingerprint", "decision_trail", "report", "cost"}


def compute_fingerprint_for(config: LongToShortVideoConfig, input_data: dict) -> str:
    import hashlib, json
    payload = {"config": config.model_dump(), "input": input_data}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


async def long_to_short_video_workflow(
    config: LongToShortVideoConfig,
    input_data: dict[str, Any],
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    findings: dict[str, Any] = {"clips": []}
    status = "completed"
    error = None
    trail: list[dict] = []
    cost = 0.0

    def step(d):
        trail.append(d)
        if on_step:
            on_step(d)

    try:
        video_path = input_data.get("video_path")
        audio_path = input_data.get("audio_path", video_path)
        step({"step": "transcribe", "status": "started"})
        # transcribe
        transcript = None
        try:
            from oprim import whisper_transcribe  # type: ignore
            transcript = await whisper_transcribe(audio_path=audio_path)
        except Exception as exc:
            step({"step": "transcribe", "status": "failed", "error": str(exc)})
            transcript = {"segments": []}
        segments = []
        if isinstance(transcript, dict):
            segments = transcript.get("segments", []) or []
        elif hasattr(transcript, "segments"):
            segments = list(transcript.segments)
        step({"step": "transcribe", "status": "done", "n_segments": len(segments)})

        # highlight
        step({"step": "highlight", "status": "started"})
        try:
            from oskill import highlight_detection_algorithm  # type: ignore
        except Exception:
            from oskill._highlight_detection_algorithm import highlight_detection_algorithm  # type: ignore
        highlights = highlight_detection_algorithm(
            segments=segments, top_k=config.top_k
        )
        step({"step": "highlight", "status": "done", "n": len(highlights)})

        # crop (best effort, needs ffmpeg)
        clips = []
        for h in highlights:
            clip_info = {"start": h["start"], "end": h["end"], "text": h["text"], "score": h["score"]}
            try:
                from oprim import crop_video_to_portrait  # type: ignore
                outp = output_dir / f"clip_{h['start']:.1f}.mp4"
                await crop_video_to_portrait(input_path=video_path, output_path=outp)
                clip_info["output"] = str(outp)
            except Exception as exc:
                clip_info["crop_error"] = str(exc)
            clips.append(clip_info)
        findings["clips"] = clips
        step({"step": "crop", "status": "done", "n": len(clips)})
    except Exception as exc:
        status = "failed"
        error = {"message": str(exc)}

    fp = compute_fingerprint_for(config, input_data) if "fingerprint" in config._enabled_pillars else None
    report_path = None
    if "report" in config._enabled_pillars:
        report_path = output_dir / "long_to_short_report.md"
        report_path.write_text(f"# Long to Short\n\nclips: {len(findings['clips'])}\n", encoding="utf-8")
    return {
        "findings": findings,
        "status": status,
        "error": error,
        "fingerprint": fp,
        "decision_trail": {"steps": trail} if "decision_trail" in config._enabled_pillars else None,
        "cost_usd": cost,
        "report_path": report_path,
    }
