"""Tests for omodul.agentic_longvideo_pipeline (M8 — ≥8 tests)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from omodul.agentic_longvideo_pipeline import (
    LongVideoConfig,
    LongVideoResult,
    _select_ref_image,
    agentic_longvideo_pipeline,
)
from oskill._schemas import ChapterScript, Chapter, SpeakerLine, ShotFrame, ReferenceSet, FrameConsistencyResult


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
