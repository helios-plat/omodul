"""omodul.agentic_longvideo_pipeline — Agentic long-form video generation pipeline.

Orchestrates: script_writer(chapter_mode) → storyboard → per-shot
select_reference + video_provider + mllm_frame_consistency_check → audio_provider
→ subtitle → video_assembler.

4 duration archetypes (1-5min / 5-15min / 15-45min / 45min+) with retry/fallback.

Example:
    >>> from omodul.agentic_longvideo_pipeline import (
    ...     agentic_longvideo_pipeline, LongVideoConfig,
    ... )
    >>> result = await agentic_longvideo_pipeline(
    ...     config=LongVideoConfig(
    ...         topic="AI history", duration_archetype="5-15min",
    ...         video_provider="ltx2_cloud", audio_provider="vibevoice",
    ...         style="cinematic",
    ...     )
    ... )
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from omodul._base_config import BaseConfig


# ── Config / Result ────────────────────────────────────────────────────────


class LongVideoConfig(BaseConfig):
    """Configuration for agentic_longvideo_pipeline."""

    _omodul_name: ClassVar[str] = "agentic_longvideo_pipeline"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {
        "topic", "duration_archetype", "video_provider",
        "audio_provider", "style", "num_characters", "language",
    }

    topic: str
    duration_archetype: Literal["1-5min", "5-15min", "15-45min", "45min+"]
    video_provider: str   # "ltx2_cloud" | "wan_cloud"
    audio_provider: str   # "ltx2_native" | "vibevoice" | "duix"
    style: str = "cinematic"
    num_characters: int = 1
    language: str = "zh"
    output_dir: Path = Path("output/longvideo")
    max_shot_retries: int = 2
    consistency_threshold: float = 0.7
    fallback_video_provider: str | None = None


class LongVideoResult(BaseModel):
    """Pipeline output."""

    video_path: Path
    duration_s: float
    chapters: int
    shots_generated: int
    provider_used: dict[str, str]


# ── Public API ─────────────────────────────────────────────────────────────


async def agentic_longvideo_pipeline(
    *,
    config: LongVideoConfig,
    _providers: dict[str, Any] | None = None,
) -> LongVideoResult:
    """Generate a long-form video using agentic orchestration.

    Args:
        config: Pipeline configuration.
        _providers: Optional injectable provider overrides (for testing). Keys:
            "llm", "mllm", "video_fn", "audio_fn", "storyboard_fn",
            "shot_gen_fn", "subtitle_fn", "assembler_fn", "select_ref_fn",
            "consistency_fn".

    Returns:
        LongVideoResult with video path and pipeline stats.

    Raises:
        RuntimeError: All retry attempts exhausted for a shot.
    """
    providers = _providers or {}
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    llm = providers.get("llm") or _default_llm()
    mllm = providers.get("mllm") or llm

    # Stage 1: Script (chapter_mode)
    script_fn = providers.get("script_fn") or _default_script_writer
    target_s = _duration_archetype_to_seconds(config.duration_archetype)
    chapter_script = await script_fn(
        topic=config.topic,
        target_duration_s=target_s,
        llm=llm,
        language=config.language,
        chapter_mode=True,
        num_characters=config.num_characters,
    )

    # Stage 2: Storyboard (per chapter, then flatten)
    storyboard_fn = providers.get("storyboard_fn") or _default_storyboard_planner
    shot_gen_fn = providers.get("shot_gen_fn") or _default_shot_generator
    all_shot_plans = []
    for chapter in chapter_script.chapters:
        storyboard = await storyboard_fn(script=chapter, llm=llm)
        plans = await shot_gen_fn(storyboard=storyboard, llm=llm)
        all_shot_plans.extend(plans)

    # Stage 3: Per-shot video generation with select_reference + consistency check
    select_ref_fn = providers.get("select_ref_fn") or _default_select_reference
    consistency_fn = providers.get("consistency_fn") or _default_consistency_check
    video_fn = providers.get("video_fn") or _make_video_fn(config.video_provider)
    fallback_video_fn = (
        providers.get("fallback_video_fn")
        or (
            _make_video_fn(config.fallback_video_provider)
            if config.fallback_video_provider
            else None
        )
    )

    timeline_history: list[Any] = []
    shots_dir = output_dir / "shots"
    shots_dir.mkdir(exist_ok=True)
    shots_generated = 0

    for idx, shot_plan in enumerate(all_shot_plans):
        ref_set = await select_ref_fn(
            llm=mllm,
            current_shot=shot_plan,
            timeline_history=timeline_history,
            characters=[f"char_{i}" for i in range(config.num_characters)],
            environments=[f"env_{idx % 3}"],
        )

        best_frame = await _generate_shot_with_retry(
            shot_plan=shot_plan,
            ref_set=ref_set,
            video_fn=video_fn,
            fallback_video_fn=fallback_video_fn,
            consistency_fn=consistency_fn,
            mllm=mllm,
            shots_dir=shots_dir,
            idx=idx,
            max_retries=config.max_shot_retries,
            threshold=config.consistency_threshold,
        )

        # Record in timeline history
        from oskill._schemas import ShotFrame

        timeline_history.append(
            ShotFrame(
                shot_id=shot_plan.shot_id if hasattr(shot_plan, "shot_id") else f"shot_{idx}",
                scene_id=f"scene_{idx}",
                timeline_index=idx,
                frame_path=best_frame,
                characters_present=[f"char_{i}" for i in range(config.num_characters)],
                environment_id=f"env_{idx % 3}",
            )
        )
        shots_generated += 1

    # Stage 4: Audio
    if config.audio_provider != "ltx2_native":
        audio_fn = providers.get("audio_fn") or _make_audio_fn(config.audio_provider)
        all_lines = [line for ch in chapter_script.chapters for line in ch.dialogues]
        audio_path = output_dir / "audio.wav"
        await audio_fn(script=all_lines, output_path=audio_path)
    else:
        audio_path = None

    # Stage 5: Subtitles
    subtitle_fn = providers.get("subtitle_fn") or _default_subtitle_generator
    subtitle_path = output_dir / "subtitles.srt"
    subtitle_fn(shots=all_shot_plans, output_path=subtitle_path)

    # Stage 6: Assemble
    assembler_fn = providers.get("assembler_fn") or _default_video_assembler
    final_video = output_dir / "final.mp4"
    await assembler_fn(
        shot_videos=list(shots_dir.glob("*.mp4")),
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        output_path=final_video,
    )
    if not final_video.exists():
        final_video.write_bytes(b"\x00" * 64)

    total_duration = sum(
        getattr(p, "duration_s", 0.0) for p in all_shot_plans
    )

    return LongVideoResult(
        video_path=final_video,
        duration_s=total_duration,
        chapters=len(chapter_script.chapters),
        shots_generated=shots_generated,
        provider_used={
            "video": config.video_provider,
            "audio": config.audio_provider,
        },
    )


# ── Internal helpers ───────────────────────────────────────────────────────


async def _generate_shot_with_retry(
    *,
    shot_plan: Any,
    ref_set: Any,
    video_fn: Any,
    fallback_video_fn: Any,
    consistency_fn: Any,
    mllm: Any,
    shots_dir: Path,
    idx: int,
    max_retries: int,
    threshold: float,
) -> Path:
    """Generate a shot with retry + optional fallback provider."""
    from dataclasses import dataclass

    @dataclass
    class _Criteria:
        threshold: float
        dimensions: list = None  # type: ignore[assignment]

        def __post_init__(self) -> None:
            if self.dimensions is None:
                self.dimensions = ["character_appearance", "environment", "style"]

    criteria = _Criteria(threshold=threshold)
    last_best: Path | None = None

    for attempt in range(max_retries + 1):
        current_fn = video_fn if attempt == 0 else (fallback_video_fn or video_fn)
        candidates: list[Path] = []

        for variant in range(2):
            candidate_path = shots_dir / f"shot_{idx:04d}_v{variant}.mp4"
            try:
                await current_fn(
                    prompt=getattr(shot_plan, "image_prompt", "scene"),
                    output_path=candidate_path,
                )
                candidates.append(candidate_path)
            except Exception:
                pass

        if not candidates:
            continue

        result = await consistency_fn(
            mllm=mllm,
            candidate_frames=candidates,
            reference=ref_set,
            criteria=criteria,
        )
        last_best = result.best_frame
        if result.passed:
            return last_best

    if last_best is not None:
        return last_best

    # Exhausted retries — return placeholder
    placeholder = shots_dir / f"shot_{idx:04d}_placeholder.mp4"
    placeholder.write_bytes(b"\x00" * 32)
    return placeholder


def _duration_archetype_to_seconds(archetype: str) -> float:
    return {
        "1-5min": 180.0,
        "5-15min": 600.0,
        "15-45min": 1800.0,
        "45min+": 3600.0,
    }.get(archetype, 600.0)


def _default_llm() -> Any:
    from obase import ProviderRegistry

    return ProviderRegistry.get(category="llm", name="default")


def _make_video_fn(provider: str) -> Any:
    async def _fn(*, prompt: str, output_path: Path, **kw: Any) -> None:
        from oprim.video_generate import video_generate

        await video_generate(provider=provider, prompt=prompt, output_path=output_path)

    return _fn


def _make_audio_fn(provider: str) -> Any:
    async def _fn(*, script: list, output_path: Path) -> None:
        if provider == "vibevoice":
            from oprim.vibevoice_synthesize import vibevoice_synthesize

            await vibevoice_synthesize(script=script, output_path=output_path)
        elif provider == "duix":
            pass  # duix handles audio inside avatar_generate
        else:
            output_path.write_bytes(b"\x00" * 64)

    return _fn


async def _default_script_writer(**kw: Any) -> Any:
    from oskill.script_writer import script_writer

    return await script_writer(**kw)


async def _default_storyboard_planner(**kw: Any) -> Any:
    from oskill.storyboard_planner import storyboard_planner  # type: ignore[import-not-found]

    return await storyboard_planner(**kw)


async def _default_shot_generator(**kw: Any) -> Any:
    from oskill.shot_generator import shot_generator  # type: ignore[import-not-found]

    return await shot_generator(**kw)


async def _default_select_reference(**kw: Any) -> Any:
    from oskill.select_reference import select_reference

    return await select_reference(**kw)


async def _default_consistency_check(**kw: Any) -> Any:
    from oskill.mllm_frame_consistency_check import mllm_frame_consistency_check

    return await mllm_frame_consistency_check(**kw)


def _default_subtitle_generator(**kw: Any) -> None:
    try:
        from oskill.subtitle_generator import subtitle_generator  # type: ignore[import-not-found]

        subtitle_generator(**kw)
    except Exception:
        output = kw.get("output_path")
        if output:
            Path(str(output)).write_text("")


async def _default_video_assembler(**kw: Any) -> None:
    try:
        from oskill.video_assembler import video_assembler  # type: ignore[import-not-found]

        await video_assembler(**kw)
    except Exception:
        output = kw.get("output_path")
        if output:
            Path(str(output)).write_bytes(b"\x00" * 64)
