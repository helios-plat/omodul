"""omodul.podcast_generation_workflow — multimodal content -> podcast audio."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel


class PodcastGenerationConfig(BaseModel):
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    output_format: str = "markdown"
    budget_usd: float = 5.0
    overwrite: bool = True
    speakers: list[str] = ["Host", "Guest"]

    _omodul_name: str = "podcast_generation_workflow"
    _omodul_version: str = "0.1.0"
    _fingerprint_fields: set = set()
    _enabled_pillars: set = {"fingerprint", "decision_trail", "report", "cost"}


def compute_fingerprint_for(config: PodcastGenerationConfig, input_data: dict) -> str:
    import hashlib, json
    return hashlib.sha256(json.dumps({"c": config.model_dump(), "i": input_data}, sort_keys=True, default=str).encode()).hexdigest()


async def podcast_generation_workflow(
    config: PodcastGenerationConfig,
    input_data: dict[str, Any],
    output_dir: Path,
    *,
    on_step: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = []
    def step(d):
        trail.append(d)
        if on_step: on_step(d)
    content = input_data.get("content", "") or input_data.get("text", "")
    llm_caller = input_data.get("llm_caller")
    tts_fn = input_data.get("tts_fn")
    status = "completed"
    error = None
    findings: dict[str, Any] = {}
    try:
        try:
            from oskill import dialogue_script_generation  # type: ignore
        except Exception:
            from oskill._dialogue_script_generation import dialogue_script_generation  # type: ignore
        script = dialogue_script_generation(content=content, speakers=config.speakers, llm_caller=llm_caller)
        step({"step": "script", "turns": len(script)})
        findings["script"] = script

        audio_paths = []
        if tts_fn is not None:
            for turn in script:
                try:
                    p = tts_fn(text=turn["text"], voice=turn["speaker"], output_path=output_dir / f"turn_{len(audio_paths)}.mp3")
                    audio_paths.append(p)
                except Exception as exc:
                    step({"step": "tts", "error": str(exc)})
        findings["audio_paths"] = [str(p) for p in audio_paths]

        if audio_paths:
            try:
                from oskill import multi_speaker_tts_merge  # type: ignore
            except Exception:
                from oskill._multi_speaker_tts_merge import multi_speaker_tts_merge  # type: ignore
            merged = multi_speaker_tts_merge(audio_paths=audio_paths, output_path=output_dir / "podcast.mp3")
            findings["merged"] = str(merged)
        step({"step": "merge", "n": len(audio_paths)})
    except Exception as exc:
        status = "failed"
        error = {"message": str(exc)}
    fp = compute_fingerprint_for(config, input_data) if "fingerprint" in config._enabled_pillars else None
    report_path = None
    if "report" in config._enabled_pillars:
        report_path = output_dir / "podcast_report.md"
        report_path.write_text(f"# Podcast\n\nturns: {len(findings.get('script', []))}\n", encoding="utf-8")
    return {
        "findings": findings,
        "status": status,
        "error": error,
        "fingerprint": fp,
        "decision_trail": {"steps": trail} if "decision_trail" in config._enabled_pillars else None,
        "cost_usd": 0.0,
        "report_path": report_path,
    }
