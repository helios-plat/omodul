from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from omodul.agentic_longvideo_pipeline import LongVideoResult
from omodul.longvideo_produce import (
    LongVideoConfig,
    compute_fingerprint_for,
    default_longvideo_shot_generator,
    longvideo_produce,
    rework_longvideo_shots,
)


@pytest.mark.asyncio
async def test_longvideo_produce_returns_standard_artifact_result(tmp_path: Path) -> None:
    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    pipeline_result = LongVideoResult(
        video_path=video,
        duration_s=12,
        chapters=1,
        shots_generated=2,
        provider_used={"video": "fake"},
    )
    with patch(
        "omodul.longvideo_produce.agentic_longvideo_pipeline",
        new=AsyncMock(return_value=pipeline_result),
    ):
        result = await longvideo_produce(
            {"duration_archetype": "short", "video_provider": "fake", "audio_provider": "fake"},
            {"topic": "test"},
            tmp_path,
        )

    assert result["status"] == "succeeded"
    assert result["artifacts"][0]["path"] == str(video)
    assert result["error"] is None


@pytest.mark.asyncio
async def test_longvideo_produce_never_fabricates_a_missing_artifact(tmp_path: Path) -> None:
    pipeline_result = LongVideoResult(
        video_path=tmp_path / "missing.mp4",
        duration_s=12,
        chapters=1,
        shots_generated=2,
        provider_used={},
    )
    with patch(
        "omodul.longvideo_produce.agentic_longvideo_pipeline",
        new=AsyncMock(return_value=pipeline_result),
    ):
        result = await longvideo_produce(
            {"duration_archetype": "short", "video_provider": "fake", "audio_provider": "fake"},
            {"topic": "test"},
            tmp_path,
        )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "ARTIFACT_MISSING"


def test_longvideo_fingerprint_does_not_contain_topic_material() -> None:
    fingerprint = compute_fingerprint_for({"video_provider": "x"}, {"topic": "private text"})
    assert "private" not in fingerprint
    assert len(fingerprint) == 64


@pytest.mark.asyncio
async def test_longvideo_produce_supports_an_injected_compatibility_renderer(tmp_path: Path) -> None:
    async def renderer(topic: str, output_dir: Path, _config: dict) -> dict:
        assert topic == "test"
        video = output_dir / "final.mp4"
        video.write_bytes(b"video")
        return {
            "url": str(video),
            "duration": 12.0,
            "metadata": {"shots": 2},
            "shots": [{"index": 0}],
            "quality": {"passed": True},
        }

    result = await longvideo_produce(
        {"video_provider": "fake"},
        {"topic": "test", "renderer": renderer},
        tmp_path,
    )

    assert result["status"] == "succeeded"
    assert result["artifacts"][0]["path"] == str(tmp_path / "final.mp4")
    assert result["report"]["shots_generated"] == 2


@pytest.mark.asyncio
async def test_public_longvideo_compatibility_hooks_do_not_require_private_imports(
    tmp_path: Path,
) -> None:
    async def fake_shot_generator(**kwargs: object) -> list[str]:
        assert kwargs == {"storyboard": "board", "llm": "llm"}
        return ["shot"]

    async def fake_rework(**kwargs: object) -> LongVideoResult:
        assert kwargs["shot_ids"] == [1]
        assert kwargs["_providers"] == {"video_fn": "fake"}
        return LongVideoResult(
            video_path=tmp_path / "final.mp4",
            duration_s=0,
            chapters=0,
            shots_generated=1,
            provider_used={},
        )

    with (
        patch(
            "omodul.longvideo_produce._legacy_default_shot_generator",
            new=AsyncMock(side_effect=fake_shot_generator),
        ),
        patch(
            "omodul.longvideo_produce._legacy_regenerate_shots",
            new=AsyncMock(side_effect=fake_rework),
        ),
    ):
        assert await default_longvideo_shot_generator(storyboard="board", llm="llm") == ["shot"]
        result = await rework_longvideo_shots(
            task_dir=tmp_path,
            shot_ids=[1],
            hints={1: "more light"},
            config=LongVideoConfig(
                topic="test",
                duration_archetype="1-5min",
                video_provider="fake",
                audio_provider="fake",
            ),
            providers={"video_fn": "fake"},
        )

    assert result.shots_generated == 1
