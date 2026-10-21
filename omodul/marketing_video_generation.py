"""omodul.marketing_video_generation — marketing video batch generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel


class MarketingVideoConfig(BaseModel):
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    output_format: str = "markdown"
    budget_usd: float = 5.0
    overwrite: bool = True
    template: dict = {}

    _omodul_name: str = "marketing_video_generation"
    _omodul_version: str = "0.1.0"
    _fingerprint_fields: set = set()
    _enabled_pillars: set = {"fingerprint", "decision_trail", "report", "cost"}


def compute_fingerprint_for(config: MarketingVideoConfig, input_data: dict) -> str:
    import hashlib, json
    return hashlib.sha256(json.dumps({"c": config.model_dump(), "i": input_data}, sort_keys=True, default=str).encode()).hexdigest()


async def marketing_video_generation(
    config: MarketingVideoConfig,
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
    status = "completed"
    error = None
    findings: dict[str, Any] = {}
    try:
        template = input_data.get("template", config.template) or {}
        assets = input_data.get("assets", {})
        variables = input_data.get("variables", {})
        try:
            from oskill import template_scene_compose, video_subtitle_align  # type: ignore
        except Exception:
            from oskill._template_scene_compose import template_scene_compose  # type: ignore
            from oskill._video_subtitle_align import video_subtitle_align  # type: ignore
        scenes = template_scene_compose(template=template, assets=assets, variables=variables)
        subs = input_data.get("subtitles", [])
        aligned = video_subtitle_align(subtitles=subs, audio_duration=input_data.get("audio_duration"))
        findings = {"scenes": scenes, "subtitles": aligned, "scene_count": len(scenes)}
        step({"step": "compose", "scenes": len(scenes)})
    except Exception as exc:
        status = "failed"
        error = {"message": str(exc)}
    fp = compute_fingerprint_for(config, input_data) if "fingerprint" in config._enabled_pillars else None
    report_path = None
    if "report" in config._enabled_pillars:
        report_path = output_dir / "marketing_video_report.md"
        report_path.write_text(f"# Marketing Video\n\nscenes: {len(findings.get('scenes', []))}\n", encoding="utf-8")
    return {
        "findings": findings,
        "status": status,
        "error": error,
        "fingerprint": fp,
        "decision_trail": {"steps": trail} if "decision_trail" in config._enabled_pillars else None,
        "cost_usd": 0.0,
        "report_path": report_path,
    }
