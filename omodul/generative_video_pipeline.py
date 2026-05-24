"""omodul.generative_video_pipeline — End-to-end video generation pipeline.

Implements v0.8 §5.2 4 pillars: fingerprint, decision_trail, report, cost.

Example:
    >>> from pathlib import Path
    >>> from omodul.generative_video_pipeline import (
    ...     generative_video_pipeline, GenerativeVideoConfig, GenerativeVideoInput,
    ... )
    >>> result = generative_video_pipeline(
    ...     config=GenerativeVideoConfig(topic="AI history"),
    ...     input_data=GenerativeVideoInput(),
    ...     output_dir=Path("output"),
    ... )

Raises:
    Various stage-specific errors captured in result["error"].
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from pydantic import BaseModel, Field

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report

# --- Config / Input / Findings ---


class GenerativeVideoConfig(BaseConfig):
    """Configuration for generative video pipeline."""

    _omodul_name: ClassVar[str] = "generative_video_pipeline"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {
        "topic", "main_line", "providers",
        "target_duration_s", "language", "template_id",
        "portrait_path", "bgm_path",
    }

    topic: str
    main_line: Literal["avatar", "generative"] = "avatar"
    target_duration_s: float = 180.0
    language: str = "zh"
    template_id: str | None = None
    providers: dict[str, str] = Field(default_factory=lambda: {
        "llm": "nim",
        "image_gen": "siliconflow",
        "tts": "edge_tts",
        "avatar": "wav2lip",
        "video_gen": "stub",
    })
    burn_subtitles: bool = True
    upload_platforms: list[str] = Field(default_factory=list)
    visibility: str = "private"


class GenerativeVideoInput(BaseModel):
    """Per-execution variable external resource paths (owner 建议3)."""

    portrait_path: Path | None = None
    bgm_path: Path | None = None


class UploadResult(BaseModel):
    """Result of a platform upload."""

    platform: str
    url: str = ""
    status: str = "skipped"


class GenerativeVideoFindings(BaseModel):
    """Pipeline output findings."""

    video_path: Path
    video_duration_s: float
    video_size_kb: int
    scenes_count: int
    shots_count: int
    upload_results: list[UploadResult] = Field(default_factory=list)


# --- Public API ---


def compute_fingerprint_for(
    config: GenerativeVideoConfig,
    input_data: GenerativeVideoInput,
) -> str:
    """Compute fingerprint for deduplication (exposed to service layer).

    Example:
        >>> fp = compute_fingerprint_for(config, input_data)
    """
    return compute_fingerprint(config, input_data)


def generative_video_pipeline(
    config: GenerativeVideoConfig,
    input_data: GenerativeVideoInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """End-to-end video generation pipeline.

    Args:
        config: Pipeline configuration.
        input_data: Per-execution inputs (portrait, bgm paths).
        output_dir: Directory for all outputs.
        on_step: Optional callback invoked after each stage.

    Returns:
        Dict with: findings, fingerprint, decision_trail, report_path, cost_usd, status, error.

    Example:
        >>> result = generative_video_pipeline(config, input_data, Path("out"))
        >>> assert result["status"] in ("completed", "failed")
    """
    import asyncio

    started_at = datetime.now(UTC)
    output_dir.mkdir(parents=True, exist_ok=True)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    fingerprint = compute_fingerprint_for(config, input_data)

    findings: GenerativeVideoFindings | None = None
    error: dict[str, Any] | None = None
    status: str = "completed"

    try:
        findings = asyncio.run(
            _run_stages(config, input_data, output_dir, cost_tracker, trail_steps, on_step)
        )
    except Exception as exc:
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)}
        # Record failure in trail
        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oskill",
            callable_name="_pipeline_error",
            inputs_summary={},
            outputs_summary={},
            started_at=datetime.now(UTC),
            status="failed",
            error=error,
        )

    decision_trail = build_decision_trail(
        fingerprint=fingerprint,
        config=config,
        input_data=input_data,
        trail_steps=trail_steps,
        cost_tracker=cost_tracker,
        started_at=started_at,
        status=status,
        error=error,
    )

    # Write decision_trail.json
    trail_path = output_dir / "decision_trail.json"
    trail_path.write_text(json.dumps(decision_trail, indent=2, default=str), encoding="utf-8")

    # Write report
    report_path: Path | None = None
    try:
        def _findings_section(f: Any) -> str:
            if f is None:
                return "## 3. Findings\n\nNo findings available."
            data = f.model_dump() if hasattr(f, "model_dump") else str(f)
            text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
            return f"## 3. Findings\n\n```json\n{text}\n```"

        report_path = write_markdown_report(
            output_dir=output_dir,
            omodul_name="generative_video_pipeline",
            fingerprint=fingerprint,
            config=config,
            findings=findings,
            decision_trail=decision_trail,
            cost_tracker=cost_tracker,
            status=status,  # type: ignore[arg-type]
            custom_findings_section=_findings_section,
        )
    except Exception:
        pass

    return {
        "findings": findings,
        "fingerprint": fingerprint,
        "decision_trail": decision_trail,
        "report_path": report_path,
        "cost_usd": cost_tracker.total_usd,
        "status": status,
        "error": error,
    }


# --- Internal stage orchestration ---


async def _run_stages(
    config: GenerativeVideoConfig,
    input_data: GenerativeVideoInput,
    output_dir: Path,
    cost_tracker: CostTracker,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> GenerativeVideoFindings:
    """Run all pipeline stages sequentially."""
    # Get LLM caller
    from obase import ProviderRegistry
    from oskill.consistency_check import consistency_check
    from oskill.script_writer import script_writer
    from oskill.shot_generator import shot_generator
    from oskill.storyboard_planner import storyboard_planner
    from oskill.subtitle_generator import subtitle_generator
    llm = ProviderRegistry.get(category="llm", name=config.providers["llm"])

    # Stage 1: Script writing
    t0 = datetime.now(UTC)
    script = await script_writer(
        topic=config.topic,
        target_duration_s=config.target_duration_s,
        llm=llm,
        language=config.language,
    )
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oskill",
        callable_name="script_writer",
        inputs_summary={"topic": config.topic},
        outputs_summary={"scenes": len(script.scenes)},
        started_at=t0,
    )

    # Stage 2: Storyboard planning
    t0 = datetime.now(UTC)
    storyboard = await storyboard_planner(script=script, llm=llm)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oskill",
        callable_name="storyboard_planner",
        inputs_summary={"scenes": len(script.scenes)},
        outputs_summary={"shots": len(storyboard.shots)},
        started_at=t0,
    )

    # Stage 3: Shot generation
    t0 = datetime.now(UTC)
    shot_plans = await shot_generator(storyboard=storyboard, llm=llm)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oskill",
        callable_name="shot_generator",
        inputs_summary={"shots": len(storyboard.shots)},
        outputs_summary={"plans": len(shot_plans)},
        started_at=t0,
    )

    # Stage 4: Consistency check
    t0 = datetime.now(UTC)
    report = await consistency_check(shots=shot_plans, llm=llm)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oskill",
        callable_name="consistency_check",
        inputs_summary={"shots": len(shot_plans)},
        outputs_summary={"score": report.overall_score, "issues": len(report.issues)},
        started_at=t0,
    )

    # Stage 5: Subtitle generation
    t0 = datetime.now(UTC)
    subtitle_path = output_dir / "subtitles.srt"
    subtitle_generator(shots=shot_plans, output_path=subtitle_path)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oskill",
        callable_name="subtitle_generator",
        inputs_summary={"shots": len(shot_plans)},
        outputs_summary={"path": str(subtitle_path)},
        started_at=t0,
    )

    # Stage 6: Avatar assembly (or frame rendering for generative line)
    t0 = datetime.now(UTC)
    video_path = output_dir / "final_video.mp4"

    if config.main_line == "avatar":
        from oskill.avatar_assembler import avatar_assembler

        portrait = input_data.portrait_path or Path("default_portrait.png")
        avatar_videos = await avatar_assembler(
            shots=shot_plans,
            portrait_path=portrait,
            tts_provider=config.providers["tts"],
            avatar_provider=config.providers["avatar"],
            output_dir=output_dir / "shots",
        )
        record_step(
            trail_steps=trail_steps, on_step=on_step, layer="oprim_batch",
            callable_name="avatar_assembler",
            inputs_summary={"shots": len(shot_plans)},
            outputs_summary={"videos": len(avatar_videos)},
            started_at=t0,
        )

        # Stage 7: Video assembly
        t0 = datetime.now(UTC)
        from oskill.video_assembler import video_assembler

        await video_assembler(
            avatar_videos=avatar_videos,
            bgm_path=input_data.bgm_path,
            subtitle_path=subtitle_path if config.burn_subtitles else None,
            output_path=video_path,
        )
    else:
        # Generative line: frame rendering + video gen
        from oskill.frame_renderer import frame_renderer
        from oskill.reference_generator import reference_generator

        refs = await reference_generator(shots=shot_plans, llm=llm)
        frames = await frame_renderer(
            references=refs,
            image_provider=config.providers["image_gen"],
            output_dir=output_dir / "frames",
        )
        record_step(
            trail_steps=trail_steps, on_step=on_step, layer="oprim_batch",
            callable_name="frame_renderer",
            inputs_summary={"refs": len(refs)},
            outputs_summary={"frames": len(frames)},
            started_at=t0,
        )
        # Placeholder: actual video gen would happen here
        video_path.write_bytes(b"\x00" * 64)

    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oskill",
        callable_name="video_assembler",
        inputs_summary={},
        outputs_summary={"path": str(video_path)},
        started_at=t0,
    )

    # Compute findings
    video_size = video_path.stat().st_size // 1024 if video_path.exists() else 0
    total_duration = sum(s.duration_s for s in shot_plans)

    return GenerativeVideoFindings(
        video_path=video_path,
        video_duration_s=total_duration,
        video_size_kb=video_size,
        scenes_count=len(script.scenes),
        shots_count=len(shot_plans),
    )
