"""Tests for omodul.agentic_longvideo_pipeline (M8 — ≥8 tests)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from oskill._schemas import (
    Chapter,
    ChapterScript,
    FrameConsistencyResult,
    ReferenceSet,
    SpeakerLine,
)

from omodul.agentic_longvideo_pipeline import (
    LongVideoConfig,
    LongVideoResult,
    _select_ref_image,
    agentic_longvideo_pipeline,
)

# ── Shared fixtures / helpers ──────────────────────────────────────────────


@dataclass
class _FakeShotPlan:
    shot_id: str
    image_prompt: str = "a scene"
    tts_text: str = "narration"
    duration_s: float = 5.0


@dataclass
class _FakeStoryboard:
    shots: list = field(default_factory=list)


def _make_chapter_script(n_chapters: int = 2, n_chars: int = 1) -> ChapterScript:
    return ChapterScript(
        chapters=[
            Chapter(
                chapter_id=f"ch_{i}",
                title=f"Chapter {i}",
                scenes=[{"idx": i}],
                dialogues=[SpeakerLine(speaker_id="s0", text="line")],
            )
            for i in range(n_chapters)
        ],
        total_duration_s=600.0,
        characters=[f"char_{j}" for j in range(n_chars)],
    )


def _base_providers(tmp_path: Path, *, n_chapters: int = 2) -> dict[str, Any]:
    """Minimal mock providers that write files as side-effects."""
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    async def _script_fn(**kw: Any) -> ChapterScript:
        return _make_chapter_script(n_chapters=n_chapters)

    async def _storyboard_fn(**kw: Any) -> _FakeStoryboard:
        return _FakeStoryboard(shots=[_FakeShotPlan(f"s{i:03d}") for i in range(2)])

    async def _shot_gen_fn(**kw: Any) -> list:
        return [_FakeShotPlan(f"sp{i:03d}") for i in range(2)]

    async def _select_ref_fn(**kw: Any) -> ReferenceSet:
        return ReferenceSet(character_refs={}, environment_refs={}, selected_from=[])

    async def _consistency_fn(**kw: Any) -> FrameConsistencyResult:
        candidates = kw.get("candidate_frames", [])
        best = candidates[0] if candidates else tmp_path / "best.mp4"
        return FrameConsistencyResult(
            best_frame=best,
            scores={str(best): 0.9},
            passed=True,
        )

    async def _video_fn(
        *, prompt: str, output_path: Path, reference_image: Path | None = None, **kw: Any
    ) -> None:
        output_path.write_bytes(b"\x00" * 32)

    async def _audio_fn(*, script: list, output_path: Path) -> None:
        output_path.write_bytes(b"\x00" * 64)

    def _subtitle_fn(**kw: Any) -> None:
        Path(str(kw["output_path"])).write_text("")

    async def _assembler_fn(**kw: Any) -> None:
        Path(str(kw["output_path"])).write_bytes(b"\x00" * 128)

    return {
        "llm": MagicMock(),
        "mllm": MagicMock(),
        "script_fn": _script_fn,
        "storyboard_fn": _storyboard_fn,
        "shot_gen_fn": _shot_gen_fn,
        "select_ref_fn": _select_ref_fn,
        "consistency_fn": _consistency_fn,
        "video_fn": _video_fn,
        "audio_fn": _audio_fn,
        "subtitle_fn": _subtitle_fn,
        "assembler_fn": _assembler_fn,
    }


def _config(
    tmp_path: Path,
    *,
    archetype: str = "5-15min",
    video: str = "ltx2_cloud",
    audio: str = "vibevoice",
) -> LongVideoConfig:
    return LongVideoConfig(
        topic="test video",
        duration_archetype=archetype,  # type: ignore[arg-type]
        video_provider=video,
        audio_provider=audio,
        style="cinematic",
        output_dir=tmp_path / "out",
    )


# ── Tests ──────────────────────────────────────────────────────────────────


class TestAgenticLongvideoPipeline:
    async def test_1_5min_e2e(self, tmp_path: Path) -> None:
        """1-5min archetype completes with LongVideoResult."""
        providers = _base_providers(tmp_path)
        result = await agentic_longvideo_pipeline(
            config=_config(tmp_path, archetype="1-5min"),
            _providers=providers,
        )
        assert isinstance(result, LongVideoResult)
        assert result.chapters >= 1
        assert result.shots_generated > 0

    async def test_5_15min_e2e_vibevoice_multi_char(self, tmp_path: Path) -> None:
        """5-15min mainstream: multi-chapter + VibeVoice audio."""
        providers = _base_providers(tmp_path, n_chapters=4)
        audio_calls: list = []

        async def _audio_fn(*, script: list, output_path: Path) -> None:
            audio_calls.append(len(script))
            output_path.write_bytes(b"\x00" * 64)

        providers["audio_fn"] = _audio_fn
        cfg = LongVideoConfig(
            topic="documentary",
            duration_archetype="5-15min",
            video_provider="ltx2_cloud",
            audio_provider="vibevoice",
            style="documentary",
            num_characters=2,
            output_dir=tmp_path / "out",
        )
        result = await agentic_longvideo_pipeline(config=cfg, _providers=providers)
        assert result.chapters == 4
        assert len(audio_calls) == 1

    async def test_15_45min_e2e(self, tmp_path: Path) -> None:
        """15-45min archetype with Duix audio provider."""
        providers = _base_providers(tmp_path, n_chapters=8)
        result = await agentic_longvideo_pipeline(
            config=_config(tmp_path, archetype="15-45min", audio="duix"),
            _providers=providers,
        )
        assert result.duration_s >= 0

    async def test_45_plus_min_e2e(self, tmp_path: Path) -> None:
        """45+min archetype with many chapters."""
        providers = _base_providers(tmp_path, n_chapters=16)
        result = await agentic_longvideo_pipeline(
            config=_config(tmp_path, archetype="45min+"),
            _providers=providers,
        )
        assert result.chapters == 16

    async def test_video_provider_ltx2_vs_wan_switch(self, tmp_path: Path) -> None:
        """video_provider field is recorded in provider_used."""
        for provider in ("ltx2_cloud", "wan_cloud"):
            providers = _base_providers(tmp_path)
            result = await agentic_longvideo_pipeline(
                config=_config(tmp_path / provider, video=provider),
                _providers=providers,
            )
            assert result.provider_used["video"] == provider

    async def test_audio_provider_switch(self, tmp_path: Path) -> None:
        """audio_provider is recorded in provider_used."""
        for audio in ("vibevoice", "duix", "ltx2_native"):
            providers = _base_providers(tmp_path)
            result = await agentic_longvideo_pipeline(
                config=_config(tmp_path / audio, audio=audio),
                _providers=providers,
            )
            assert result.provider_used["audio"] == audio

    async def test_retry_fallback_on_shot_failure(self, tmp_path: Path) -> None:
        """Shot generation fails once, falls back on retry → still produces result."""
        providers = _base_providers(tmp_path)
        call_count = 0

        async def _flaky_video(*, prompt: str, output_path: Path, **kw: Any) -> None:
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 1:
                raise RuntimeError("provider timeout")
            output_path.write_bytes(b"\x00" * 32)

        providers["video_fn"] = _flaky_video
        result = await agentic_longvideo_pipeline(
            config=_config(tmp_path),
            _providers=providers,
        )
        assert result.shots_generated > 0

    async def test_mllm_check_failed_triggers_regen(self, tmp_path: Path) -> None:
        """When consistency check fails, candidates are regenerated (passed=False then True)."""
        providers = _base_providers(tmp_path)
        check_calls = 0

        async def _consistency_fn(**kw: Any) -> FrameConsistencyResult:
            nonlocal check_calls
            check_calls += 1
            candidates = kw.get("candidate_frames", [])
            best = candidates[0] if candidates else tmp_path / "best.mp4"
            # first call fails, subsequent calls pass
            passed = check_calls > 1
            return FrameConsistencyResult(
                best_frame=best, scores={str(best): 0.9 if passed else 0.2}, passed=passed
            )

        providers["consistency_fn"] = _consistency_fn
        result = await agentic_longvideo_pipeline(
            config=_config(tmp_path),
            _providers=providers,
        )
        assert result.shots_generated > 0
        assert check_calls > 0


# ── P0-1 reference-frame conditioning ───────────────────────────────────────


class TestSelectRefImage:
    """Unit tests for the ref_set → single i2v frame collapse policy."""

    def test_none_ref_set(self) -> None:
        assert _select_ref_image(None) is None

    def test_empty_refs_returns_none(self) -> None:
        rs = ReferenceSet(character_refs={}, environment_refs={}, selected_from=[])
        assert _select_ref_image(rs) is None

    def test_character_priority_picks_lowest_sorted_id(self, tmp_path: Path) -> None:
        # char_0 must win over char_1 regardless of dict insertion order.
        rs = ReferenceSet(
            character_refs={"char_1": tmp_path / "c1.png", "char_0": tmp_path / "c0.png"},
            environment_refs={"env_0": tmp_path / "e0.png"},
            selected_from=["s0"],
        )
        assert _select_ref_image(rs) == tmp_path / "c0.png"

    def test_environment_fallback_when_no_character(self, tmp_path: Path) -> None:
        rs = ReferenceSet(
            character_refs={},
            environment_refs={"env_1": tmp_path / "e1.png", "env_0": tmp_path / "e0.png"},
            selected_from=["s0"],
        )
        assert _select_ref_image(rs) == tmp_path / "e0.png"


class TestRefSetThreadedToVideoFn:
    """E2E: ref_set must reach video_fn as reference_image (i2v conditioning)."""

    async def test_character_ref_threaded_as_i2v(self, tmp_path: Path) -> None:
        providers = _base_providers(tmp_path)
        ref_png = tmp_path / "char0_ref.png"
        ref_png.write_bytes(b"\x89PNG")
        received: list[Path | None] = []

        async def _select_ref_fn(**kw: Any) -> ReferenceSet:
            return ReferenceSet(
                character_refs={"char_1": tmp_path / "c1.png", "char_0": ref_png},
                environment_refs={"env_0": tmp_path / "e0.png"},
                selected_from=["s0"],
            )

        async def _capturing_video_fn(
            *, prompt: str, output_path: Path, reference_image: Path | None = None, **kw: Any
        ) -> None:
            received.append(reference_image)
            output_path.write_bytes(b"\x00" * 32)

        providers["select_ref_fn"] = _select_ref_fn
        providers["video_fn"] = _capturing_video_fn

        await agentic_longvideo_pipeline(config=_config(tmp_path), _providers=providers)

        assert received, "video_fn was never called"
        # every generation conditioned on the primary character reference (char_0)
        assert all(r == ref_png for r in received)

    async def test_no_refs_threads_none(self, tmp_path: Path) -> None:
        # Backward compat: empty ref_set → reference_image stays None (t2v).
        providers = _base_providers(tmp_path)
        received: list[Path | None] = []

        async def _capturing_video_fn(
            *, prompt: str, output_path: Path, reference_image: Path | None = None, **kw: Any
        ) -> None:
            received.append(reference_image)
            output_path.write_bytes(b"\x00" * 32)

        providers["video_fn"] = _capturing_video_fn  # base _select_ref_fn returns empty set
        await agentic_longvideo_pipeline(config=_config(tmp_path), _providers=providers)

        assert received, "video_fn was never called"
        assert all(r is None for r in received)

    async def test_default_make_video_fn_forwards_reference_image(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Locks the *production* closure (the original no-op stripped the param).
        from omodul.agentic_longvideo_pipeline import _make_video_fn

        seen: dict[str, Any] = {}

        async def _fake_video_generate(**kw: Any) -> Path:
            seen.update(kw)
            Path(kw["output_path"]).write_bytes(b"\x00")
            return Path(kw["output_path"])

        import oprim.video_generate as vg_mod

        monkeypatch.setattr(vg_mod, "video_generate", _fake_video_generate)

        fn = _make_video_fn("wan_cloud")
        ref = tmp_path / "ref.png"
        await fn(prompt="hello", output_path=tmp_path / "o.mp4", reference_image=ref)

        assert seen["reference_image"] == ref
        assert seen["provider"] == "wan_cloud"


# ── RFC-003: 镜头生成并发窗口 ────────────────────────────────────────────────


def _concurrency_probe_providers(tmp_path: Path) -> tuple[dict[str, Any], dict[str, int]]:
    """在 _base_providers 基础上,用可观测并发的 video_fn 替换,记录峰值并发。"""
    import asyncio

    providers = _base_providers(tmp_path)
    stat = {"inflight": 0, "peak": 0}

    async def _probe_video_fn(
        *, prompt: str, output_path: Path, reference_image: Path | None = None, **kw: Any
    ) -> None:
        stat["inflight"] += 1
        stat["peak"] = max(stat["peak"], stat["inflight"])
        await asyncio.sleep(0.02)  # 制造重叠窗口
        output_path.write_bytes(b"\x00" * 32)
        stat["inflight"] -= 1

    providers["video_fn"] = _probe_video_fn
    return providers, stat


async def test_max_concurrent_shots_default_is_sequential(tmp_path: Path) -> None:
    """默认 max_concurrent_shots=1 → 任意时刻至多 1 个镜头在生成(向后兼容)。"""
    providers, stat = _concurrency_probe_providers(tmp_path)
    cfg = LongVideoConfig(
        topic="t",
        duration_archetype="5-15min",
        video_provider="ltx2_cloud",
        audio_provider="vibevoice",
        output_dir=tmp_path / "out",
    )
    assert cfg.max_concurrent_shots == 1
    res = await agentic_longvideo_pipeline(config=cfg, _providers=providers)
    assert stat["peak"] == 1
    assert res.shots_generated == 4  # 2 章 × 2 镜头


async def test_max_concurrent_shots_runs_in_parallel(tmp_path: Path) -> None:
    """max_concurrent_shots=2 → 窗口内并发(峰值并发达 2),且镜头数/顺序不变。"""
    providers, stat = _concurrency_probe_providers(tmp_path)
    cfg = LongVideoConfig(
        topic="t",
        duration_archetype="5-15min",
        video_provider="ltx2_cloud",
        audio_provider="vibevoice",
        output_dir=tmp_path / "out",
        max_concurrent_shots=2,
    )
    res = await agentic_longvideo_pipeline(config=cfg, _providers=providers)
    assert stat["peak"] == 2  # 真并发
    assert res.shots_generated == 4  # 总数不变


# ── B9/B10: 装配有序去重 + audio 降级 ─────────────────────────────────────────


async def test_assembly_dedups_variants_and_orders(tmp_path: Path) -> None:
    """B9: 每镜头恒写 2 个变体(_v0/_v1)到 shots_dir,但装配只收每镜头最佳帧、按序、不重复。"""
    providers = _base_providers(tmp_path)  # 2 章 × 2 镜头 = 4 shots
    captured: dict = {}

    async def _assembler_fn(**kw: Any) -> None:
        captured["shot_videos"] = list(kw["shot_videos"])
        Path(str(kw["output_path"])).write_bytes(b"\x00" * 128)

    providers["assembler_fn"] = _assembler_fn
    res = await agentic_longvideo_pipeline(config=_config(tmp_path), _providers=providers)

    sv = captured["shot_videos"]
    # 每镜头恰好 1 个(而非 shots_dir 里的 2 个变体)
    assert len(sv) == res.shots_generated
    # shots_dir 实际有 2×N 个 .mp4(证明确实发生了去重,而非碰巧只写了 N 个)
    shots_dir = (tmp_path / "out") / "shots"
    assert len(list(shots_dir.glob("*.mp4"))) == 2 * res.shots_generated
    # 按镜头序号严格有序 0..N-1
    idxs = [int(Path(p).name.split("_")[1]) for p in sv]
    assert idxs == list(range(res.shots_generated))


async def test_audio_failure_degrades_to_video_only(tmp_path: Path) -> None:
    """B10: 配音失败 → 纯视频出片(audio_path=None),整链不崩。"""
    providers = _base_providers(tmp_path)
    captured: dict = {}

    async def _failing_audio(*, script: list, output_path: Path) -> None:
        raise RuntimeError("tts backend down")

    async def _assembler_fn(**kw: Any) -> None:
        captured["audio_path"] = kw["audio_path"]
        Path(str(kw["output_path"])).write_bytes(b"\x00" * 128)

    providers["audio_fn"] = _failing_audio
    providers["assembler_fn"] = _assembler_fn

    res = await agentic_longvideo_pipeline(
        config=_config(tmp_path, audio="vibevoice"), _providers=providers
    )
    assert isinstance(res, LongVideoResult)  # 没崩
    assert captured["audio_path"] is None  # 降级为纯视频


def test_default_llm_uses_obase_singleton_generic(monkeypatch: Any) -> None:
    """B13: _default_llm 经 get().generic('llm','default') 取,而非 get(category=)。"""
    import obase

    from omodul.agentic_longvideo_pipeline import _default_llm

    calls: dict = {}

    class _Reg:
        def generic(self, category: str, name: str = "default") -> str:
            calls["args"] = (category, name)
            return "LLM_OBJ"

    class _PR:
        @classmethod
        def get(cls) -> _Reg:
            return _Reg()

    monkeypatch.setattr(obase, "ProviderRegistry", _PR)
    assert _default_llm() == "LLM_OBJ"
    assert calls["args"] == ("llm", "default")


# ── B11/B12/B14: 失败暴露 + 显式时长/short + per-shot prompt 钩子 ──────────────


def test_short_archetype_maps_to_10s() -> None:
    """B12: 'short' 档映射 10s。"""
    from omodul.agentic_longvideo_pipeline import _duration_archetype_to_seconds

    assert _duration_archetype_to_seconds("short") == 10.0


async def test_explicit_target_duration_overrides_archetype(tmp_path: Path) -> None:
    """B12: config.target_duration_s 覆盖档位映射,传给 script_fn。"""
    providers = _base_providers(tmp_path)
    seen: dict = {}
    _orig = providers["script_fn"]

    async def _script_fn(**kw: Any) -> Any:
        seen["target"] = kw.get("target_duration_s")
        return await _orig(**kw)

    providers["script_fn"] = _script_fn
    cfg = LongVideoConfig(
        topic="t",
        duration_archetype="5-15min",
        video_provider="ltx2_cloud",
        audio_provider="vibevoice",
        output_dir=tmp_path / "out",
        target_duration_s=42.0,
    )
    await agentic_longvideo_pipeline(config=cfg, _providers=providers)
    assert seen["target"] == 42.0


async def test_failed_shots_exposed_not_silent(tmp_path: Path) -> None:
    """B11: 全部镜头生成失败 → 回退 placeholder,failed_shots 暴露(不再静默),链不崩。"""
    providers = _base_providers(tmp_path)

    async def _failing_video(*, prompt: str, output_path: Path, **kw: Any) -> None:
        raise RuntimeError("gpu oom")

    providers["video_fn"] = _failing_video
    res = await agentic_longvideo_pipeline(config=_config(tmp_path), _providers=providers)
    assert res.failed_shots == list(range(res.shots_generated))
    assert len(res.failed_shots) > 0


async def test_shot_prompt_fn_hook_overrides_prompt(tmp_path: Path) -> None:
    """B14: 提供 shot_prompt_fn → 每镜头 prompt 走钩子(hevi 提示词工程)。"""
    providers = _base_providers(tmp_path)
    seen_prompts: list[str] = []

    async def _capturing_video(*, prompt: str, output_path: Path, **kw: Any) -> None:
        seen_prompts.append(prompt)
        output_path.write_bytes(b"\x00" * 32)

    async def _shot_prompt_fn(*, shot_plan: Any, idx: int) -> str:
        return f"ENGINEERED_{idx}"

    providers["video_fn"] = _capturing_video
    providers["shot_prompt_fn"] = _shot_prompt_fn
    await agentic_longvideo_pipeline(config=_config(tmp_path), _providers=providers)
    assert any(p.startswith("ENGINEERED_") for p in seen_prompts)


# ── C3: 结构化 per-shot 结果 + 镜头级返工 ──────────────────────────────────


async def test_c3_result_exposes_shot_records(tmp_path: Path) -> None:
    """C3a: LongVideoResult.shots 暴露 per-shot 明细(此前只有 shots_generated 计数)。"""
    providers = _base_providers(tmp_path)
    result = await agentic_longvideo_pipeline(config=_config(tmp_path), _providers=providers)
    assert len(result.shots) == result.shots_generated
    assert result.shots
    r0 = result.shots[0]
    assert r0.index == 0
    assert r0.provider == "ltx2_cloud"
    assert r0.variant_chosen == 0  # mock consistency 选 candidates[0]
    assert r0.passed is True
    assert isinstance(r0.consistency_score, float)  # mock scores {best: 0.9}


async def test_c3_shot_plan_sidecars_persisted(tmp_path: Path) -> None:
    """C3: 每镜头落 plan 边车,供 regenerate_shots 用原 prompt。"""
    import json

    providers = _base_providers(tmp_path)
    await agentic_longvideo_pipeline(config=_config(tmp_path), _providers=providers)
    sidecars = sorted((tmp_path / "out" / "shots").glob("shot_*.plan.json"))
    assert len(sidecars) >= 1
    data = json.loads(sidecars[0].read_text())
    assert "prompt" in data and "index" in data


async def test_c3_regenerate_shots_only_targets_with_hint(tmp_path: Path) -> None:
    """C3b: regenerate_shots 只重生成指定镜头 + hints 并入 prompt;其余镜头复用。"""
    from omodul.agentic_longvideo_pipeline import regenerate_shots

    providers = _base_providers(tmp_path)
    cfg = _config(tmp_path)
    await agentic_longvideo_pipeline(config=cfg, _providers=providers)

    regen_calls: list[tuple[str, str]] = []

    async def _tracking_video(*, prompt: str, output_path: Path, **kw: Any) -> None:
        regen_calls.append((prompt, Path(output_path).name))
        output_path.write_bytes(b"\x00" * 48)

    providers["video_fn"] = _tracking_video
    result = await regenerate_shots(
        task_dir=tmp_path / "out",
        shot_ids=[1],
        hints={1: "brighter lighting"},
        config=cfg,
        _providers=providers,
    )
    assert regen_calls, "regen 应调用 video_fn"
    assert all(name.startswith("shot_0001_") for _, name in regen_calls)  # 只重生成 shot 1
    assert all("brighter lighting" in p for p, _ in regen_calls)  # hint 并入 prompt
    n_shots = len(sorted((tmp_path / "out" / "shots").glob("shot_*.plan.json")))
    assert len(result.shots) == n_shots  # 覆盖全部(重生成 + 复用)
    assert any(r.index == 1 for r in result.shots)
