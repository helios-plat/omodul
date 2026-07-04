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
import json
import logging
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

from omodul._base_config import BaseConfig

logger = logging.getLogger(__name__)

# ── Config / Result ────────────────────────────────────────────────────────


class LongVideoConfig(BaseConfig):
    """Configuration for agentic_longvideo_pipeline."""

    _omodul_name: ClassVar[str] = "agentic_longvideo_pipeline"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {
        "topic",
        "duration_archetype",
        "video_provider",
        "audio_provider",
        "style",
        "num_characters",
        "language",
    }

    topic: str
    duration_archetype: Literal["short", "1-5min", "5-15min", "15-45min", "45min+"]
    video_provider: str  # "ltx2_cloud" | "wan_cloud"
    audio_provider: str  # "ltx2_native" | "vibevoice" | "duix"
    style: str = "cinematic"
    num_characters: int = 1
    language: str = "zh"
    output_dir: Path = Path("output/longvideo")
    max_shot_retries: int = 2
    consistency_threshold: float = 0.7
    fallback_video_provider: str | None = None
    # RFC-003: 镜头生成并发窗口。1 = 严格顺序(默认,行为与历史版本逐字节一致);
    # >1 = 按窗口并发生成,窗口内共享窗口起点的 timeline_history 快照,跨窗口保持
    # 完整帧链连续性。单 GPU 工况应保持 1;云 provider 可调大以近线性加速。
    max_concurrent_shots: int = 1
    # B12: 显式目标时长(秒);设置则覆盖 duration_archetype 档位映射(支持任意时长 / "short" 短档)。
    target_duration_s: float | None = None


class ShotRecord(BaseModel):
    """C3: per-shot 结果明细。此前循环内算出选中变体/一致性分/及格与否后即弃(只留计数);
    下游(镜头级选优落库 / verdict→返工 / Editor)需这些。variant_chosen/consistency_score
    在拿不到时降级 -1 / None(不强依赖下游注入富对象)。"""

    index: int
    path: Path
    provider: str
    variant_chosen: int = -1
    consistency_score: float | None = None
    passed: bool = True
    duration_s: float | None = None


class LongVideoResult(BaseModel):
    """Pipeline output."""

    video_path: Path
    duration_s: float
    chapters: int
    shots_generated: int
    provider_used: dict[str, str]
    # B11: 生成失败(回退 placeholder)的镜头 idx,供上层决定 fallback/报错(此前静默吞掉)。
    failed_shots: list[int] = []
    # C3: per-shot 结果明细(默认空 → 老调用零变化)。
    shots: list[ShotRecord] = []


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
    # B12: 显式 target_duration_s 优先,否则按档位映射。
    target_s = config.target_duration_s or _duration_archetype_to_seconds(config.duration_archetype)
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
    # B14: 可选 per-shot prompt 钩子 —— 提供则用它构造/改写每镜头 prompt(hevi 提示词工程),
    # 否则沿用 shot_plan.image_prompt(默认行为不变)。签名: async (shot_plan, idx) -> str。
    shot_prompt_fn = providers.get("shot_prompt_fn")
    consistency_fn = providers.get("consistency_fn") or _default_consistency_check
    video_fn = providers.get("video_fn") or _make_video_fn(config.video_provider)
    fallback_video_fn = providers.get("fallback_video_fn") or (
        _make_video_fn(config.fallback_video_provider) if config.fallback_video_provider else None
    )

    timeline_history: list[Any] = []
    shots_dir = output_dir / "shots"
    shots_dir.mkdir(exist_ok=True)
    shots_generated = 0
    failed_shots: list[int] = []  # B11: 回退 placeholder 的镜头 idx
    shot_records: list[ShotRecord] = []  # C3: per-shot 明细

    from oskill._schemas import ShotFrame

    async def _process_shot(idx: int, shot_plan: Any, hist_snapshot: list[Any]) -> Any:
        """选参考 + 生成单镜头。hist_snapshot 为窗口起点的 timeline_history 快照。"""
        ref_set = await select_ref_fn(
            llm=mllm,
            current_shot=shot_plan,
            timeline_history=hist_snapshot,
            characters=[f"char_{i}" for i in range(config.num_characters)],
            environments=[f"env_{idx % 3}"],
        )
        prompt_override = (
            await shot_prompt_fn(shot_plan=shot_plan, idx=idx) if shot_prompt_fn else None
        )
        best_frame, shot_meta = await _generate_shot_with_retry(
            shot_plan=shot_plan,
            prompt_override=prompt_override,
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
        # C3: 持久化本镜头 prompt,供 regenerate_shots 用原 prompt + hints 定向重生成。
        _persist_shot_plan(shots_dir, idx, prompt_override, shot_plan)
        return idx, shot_plan, best_frame, shot_meta

    # RFC-003: 窗口并发。W=1 时与历史顺序实现等价(单元素窗口、快照即当前 history);
    # W>1 时窗口内并发生成、窗口内共享起点参考,跨窗口保持完整帧链连续性。
    _window_size = max(1, getattr(config, "max_concurrent_shots", 1))
    _indexed_plans = list(enumerate(all_shot_plans))
    for _base in range(0, len(_indexed_plans), _window_size):
        _window = _indexed_plans[_base : _base + _window_size]
        _snapshot = list(timeline_history)  # 窗口起点连续性快照
        if _window_size == 1:
            _results = [await _process_shot(_window[0][0], _window[0][1], _snapshot)]
        else:
            _results = list(
                await asyncio.gather(*[_process_shot(_i, _p, _snapshot) for _i, _p in _window])
            )
        # 有序回填(保证拼接顺序 + 与顺序实现的 timeline 一致)。
        for idx, shot_plan, best_frame, shot_meta in sorted(_results, key=lambda r: r[0]):
            if "placeholder" in Path(best_frame).name:  # B11: 记录生成失败(回退 placeholder)的镜头
                failed_shots.append(idx)
                logger.warning("shot %d generation failed; used placeholder", idx)
            # C3: 收集 per-shot 明细。used_fallback → 记 fallback provider 名。
            _shot_provider = (
                config.fallback_video_provider
                if shot_meta.get("used_fallback")
                else config.video_provider
            ) or config.video_provider
            shot_records.append(
                ShotRecord(
                    index=idx,
                    path=Path(best_frame),
                    provider=_shot_provider,
                    variant_chosen=shot_meta.get("variant_chosen", -1),
                    consistency_score=shot_meta.get("consistency_score"),
                    passed=bool(shot_meta.get("passed", True)),
                    duration_s=getattr(shot_plan, "duration_s", None),
                )
            )
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

    # Stage 4: Audio (B10: 配音非必需 —— 失败则降级为纯视频出片,不让整链崩)
    if config.audio_provider != "ltx2_native":
        audio_fn = providers.get("audio_fn") or _make_audio_fn(config.audio_provider)
        all_lines = [line for ch in chapter_script.chapters for line in ch.dialogues]
        audio_path = output_dir / "audio.wav"
        try:
            await audio_fn(script=all_lines, output_path=audio_path)
            if not audio_path.exists():
                raise RuntimeError("audio_fn produced no output file")
        except Exception as exc:
            logger.warning(
                "audio synthesis failed (provider=%s); degrading to video-only: %s",
                config.audio_provider,
                exc,
            )
            audio_path = None
    else:
        audio_path = None

    # Stage 5: Subtitles
    subtitle_fn = providers.get("subtitle_fn") or _default_subtitle_generator
    subtitle_path = output_dir / "subtitles.srt"
    subtitle_fn(shots=all_shot_plans, output_path=subtitle_path)

    # Stage 6: Assemble
    assembler_fn = providers.get("assembler_fn") or _default_video_assembler
    final_video = output_dir / "final.mp4"
    # B9: 按镜头序装配 —— 用 timeline_history 记录的每镜头最佳帧(已按 idx 有序、每序号唯一),
    # 而非 glob("*.mp4")(会把重试变体 shot_XXXX_v1.mp4 / placeholder 都乱序纳入,留废片/乱序)。
    await assembler_fn(
        shot_videos=[sf.frame_path for sf in timeline_history],
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        output_path=final_video,
    )
    if not final_video.exists():
        final_video.write_bytes(b"\x00" * 64)

    total_duration = sum(getattr(p, "duration_s", 0.0) for p in all_shot_plans)

    return LongVideoResult(
        video_path=final_video,
        duration_s=total_duration,
        chapters=len(chapter_script.chapters),
        shots_generated=shots_generated,
        provider_used={
            "video": config.video_provider,
            "audio": config.audio_provider,
        },
        failed_shots=failed_shots,
        shots=shot_records,
    )


async def regenerate_shots(
    *,
    task_dir: Path,
    shot_ids: list[int],
    hints: dict[int, str] | None = None,
    config: LongVideoConfig,
    _providers: dict[str, Any] | None = None,
) -> LongVideoResult:
    """C3: 只重生成 `shot_ids`,其余镜头复用既有产物,再重装配。

    读 `task_dir/shots/shot_XXXX.plan.json`(主管线落的原 prompt 边车):
      - 目标镜头:用 `原 prompt + hints[idx]` 重生成(清旧变体);
      - 非目标镜头:复用现有最佳变体(最大的 `shot_XXXX_v*.mp4`),字节不变。
    然后走注入的 `assembler_fn` 从有序 best-frame 重装配(复用现有 audio/subtitle 若在)。
    支撑下游 verdict→定向返工闭环(不必整片重烧)。需先跑过一次主管线(有边车)。
    """
    from types import SimpleNamespace

    from oskill._schemas import ReferenceSet

    providers = _providers or {}
    hints = hints or {}
    task_dir = Path(task_dir)
    shots_dir = task_dir / "shots"
    plan_files = sorted(shots_dir.glob("shot_*.plan.json"))
    if not plan_files:
        raise FileNotFoundError(
            f"no shot plan sidecars in {shots_dir}; run agentic_longvideo_pipeline first"
        )

    video_fn = providers.get("video_fn") or _make_video_fn(config.video_provider)
    fallback_video_fn = providers.get("fallback_video_fn") or (
        _make_video_fn(config.fallback_video_provider) if config.fallback_video_provider else None
    )
    consistency_fn = providers.get("consistency_fn") or _default_consistency_check
    mllm = providers.get("mllm") or providers.get("llm") or _default_llm()

    target = set(shot_ids)
    records: list[ShotRecord] = []
    ordered: list[tuple[int, Path]] = []

    for pf in plan_files:
        data = json.loads(pf.read_text())
        idx = int(data["index"])
        if idx in target:
            prompt = data.get("prompt", "scene")
            if idx in hints:
                prompt = f"{prompt} {hints[idx]}"
            for old in shots_dir.glob(f"shot_{idx:04d}_v*.mp4"):
                old.unlink(missing_ok=True)
            plan = SimpleNamespace(shot_id=data.get("shot_id", f"shot_{idx}"), image_prompt=prompt)
            best, meta = await _generate_shot_with_retry(
                shot_plan=plan,
                prompt_override=prompt,
                ref_set=ReferenceSet(character_refs={}, environment_refs={}, selected_from=[]),
                video_fn=video_fn,
                fallback_video_fn=fallback_video_fn,
                consistency_fn=consistency_fn,
                mllm=mllm,
                shots_dir=shots_dir,
                idx=idx,
                max_retries=config.max_shot_retries,
                threshold=config.consistency_threshold,
            )
            _persist_shot_plan(shots_dir, idx, prompt, plan)  # 边车更新(含 hint)
        else:
            reused = _existing_best_shot(shots_dir, idx)
            if reused is None:
                reused = shots_dir / f"shot_{idx:04d}_placeholder.mp4"
                if not reused.exists():
                    reused.write_bytes(b"\x00" * 32)
                meta = {"variant_chosen": -1, "consistency_score": None, "passed": False}
            else:
                meta = {"variant_chosen": -1, "consistency_score": None, "passed": True}
            best = reused
        records.append(
            ShotRecord(
                index=idx,
                path=Path(best),
                provider=config.video_provider,
                variant_chosen=meta.get("variant_chosen", -1),
                consistency_score=meta.get("consistency_score"),
                passed=bool(meta.get("passed", True)),
            )
        )
        ordered.append((idx, Path(best)))

    ordered.sort(key=lambda t: t[0])
    audio_path = task_dir / "audio.wav"
    subtitle_path = task_dir / "subtitles.srt"
    assembler_fn = providers.get("assembler_fn") or _default_video_assembler
    final_video = task_dir / "final.mp4"
    await assembler_fn(
        shot_videos=[p for _, p in ordered],
        audio_path=audio_path if audio_path.exists() else None,
        subtitle_path=subtitle_path if subtitle_path.exists() else None,
        output_path=final_video,
    )
    if not final_video.exists():
        final_video.write_bytes(b"\x00" * 64)

    return LongVideoResult(
        video_path=final_video,
        duration_s=0.0,
        chapters=0,
        shots_generated=len(ordered),
        provider_used={"video": config.video_provider, "audio": config.audio_provider},
        failed_shots=[r.index for r in records if not r.passed],
        shots=records,
    )


# ── Internal helpers ───────────────────────────────────────────────────────


def _existing_best_shot(shots_dir: Path, idx: int) -> Path | None:
    """C3: 复用某镜头的现有最佳变体 —— 取最大的 `shot_XXXX_v*.mp4`(排除 placeholder)。
    最佳变体在主管线运行时只存于内存,磁盘无标记;沿用 hevi 装配去重的"最大文件"启发。"""
    variants = [
        p
        for p in shots_dir.glob(f"shot_{idx:04d}_v*.mp4")
        if "placeholder" not in p.name and p.stat().st_size > 0
    ]
    if not variants:
        return None
    return max(variants, key=lambda p: p.stat().st_size)


def _extract_consistency_score(result: Any, best_frame: Path) -> float | None:
    """C3: 从 consistency_fn 结果尽力抽一个数值分。
    - 下游注入的评分卡:`result.scorecard.identity_score`。
    - 默认 mllm_frame_consistency_check:`result.scores[best_frame]`(或最大值)。
    - 都没有 → None(不强依赖下游富对象)。"""
    sc = getattr(result, "scorecard", None)
    ident = getattr(sc, "identity_score", None) if sc is not None else None
    if ident is not None:
        return float(ident)
    scores = getattr(result, "scores", None)
    if isinstance(scores, dict) and scores:
        try:
            return float(scores.get(str(best_frame), max(scores.values())))
        except (TypeError, ValueError):
            return None
    return None


def _persist_shot_plan(
    shots_dir: Path, idx: int, prompt_override: str | None, shot_plan: Any
) -> None:
    """C3: 落一个 per-shot prompt 边车(`shot_XXXX.plan.json`),供 regenerate_shots
    用原 prompt + hints 定向重生成。best-effort,失败不影响出片。"""
    prompt = prompt_override or getattr(shot_plan, "image_prompt", "scene")
    try:
        (shots_dir / f"shot_{idx:04d}.plan.json").write_text(
            json.dumps(
                {
                    "index": idx,
                    "prompt": prompt,
                    "shot_id": getattr(shot_plan, "shot_id", f"shot_{idx}"),
                },
                ensure_ascii=False,
            )
        )
    except OSError as e:
        logger.warning("shot %d plan sidecar persist failed: %s", idx, e)


def _select_ref_image(ref_set: Any) -> Path | None:
    """Collapse a ReferenceSet to a single i2v conditioning frame.

    Policy (P0-1 continuity): character reference takes priority — the lowest
    sorted character_id (e.g. char_0 before char_1) is the primary subject —
    falling back to the first environment reference, then None. i2v accepts only
    one image, so a multi-ref set must be reduced deterministically here.
    """
    if ref_set is None:
        return None
    char_refs = getattr(ref_set, "character_refs", None) or {}
    if char_refs:
        return char_refs[sorted(char_refs)[0]]
    env_refs = getattr(ref_set, "environment_refs", None) or {}
    if env_refs:
        return env_refs[sorted(env_refs)[0]]
    return None


async def _generate_shot_with_retry(
    *,
    shot_plan: Any,
    prompt_override: str | None = None,
    ref_set: Any,
    video_fn: Any,
    fallback_video_fn: Any,
    consistency_fn: Any,
    mllm: Any,
    shots_dir: Path,
    idx: int,
    max_retries: int,
    threshold: float,
) -> tuple[Path, dict[str, Any]]:
    """Generate a shot with retry + optional fallback provider.

    C3: 除 best_frame 外一并返回元数据 dict {variant_chosen, consistency_score,
    passed, used_fallback},供上层收集 ShotRecord(此前只返回 Path,元数据即弃)。
    """
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
    last_meta: dict[str, Any] = {}  # C3

    # Condition generation on the selected reference frame (P0-1): ref_set was
    # previously used only for post-hoc consistency scoring; now it also feeds
    # the provider as an i2v init image for shot-to-shot continuity.
    ref_image = _select_ref_image(ref_set)

    for attempt in range(max_retries + 1):
        current_fn = video_fn if attempt == 0 else (fallback_video_fn or video_fn)
        candidates: list[Path] = []

        for variant in range(2):
            candidate_path = shots_dir / f"shot_{idx:04d}_v{variant}.mp4"
            try:
                await current_fn(
                    prompt=prompt_override or getattr(shot_plan, "image_prompt", "scene"),
                    output_path=candidate_path,
                    reference_image=ref_image,
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
        last_meta = {
            "variant_chosen": candidates.index(last_best) if last_best in candidates else -1,
            "consistency_score": _extract_consistency_score(result, last_best),
            "passed": bool(getattr(result, "passed", True)),
            "used_fallback": attempt > 0 and fallback_video_fn is not None,
        }
        if result.passed:
            return last_best, last_meta

    if last_best is not None:
        return last_best, last_meta

    # Exhausted retries — return placeholder
    placeholder = shots_dir / f"shot_{idx:04d}_placeholder.mp4"
    placeholder.write_bytes(b"\x00" * 32)
    return placeholder, {
        "variant_chosen": -1,
        "consistency_score": None,
        "passed": False,
        "used_fallback": fallback_video_fn is not None,
    }


def _duration_archetype_to_seconds(archetype: str) -> float:
    return {
        "short": 10.0,  # B12: hevi 短档(单镜头级速览)
        "1-5min": 180.0,
        "5-15min": 600.0,
        "15-45min": 1800.0,
        "45min+": 3600.0,
    }.get(archetype, 600.0)


def _default_llm() -> Any:
    from obase import ProviderRegistry

    # B13: obase ProviderRegistry.get() 是无参单例访问器,provider 经 .generic(category, name)
    # 取(此前 get(category=, name=) → TypeError,与 obase 单例 API 不兼容)。
    return ProviderRegistry.get().generic("llm", "default")


def _make_video_fn(provider: str) -> Any:
    async def _fn(
        *,
        prompt: str,
        output_path: Path,
        reference_image: Path | None = None,
        **kw: Any,
    ) -> None:
        from oprim.video_generate import video_generate

        await video_generate(
            provider=provider,
            prompt=prompt,
            output_path=output_path,
            reference_image=reference_image,
        )

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
