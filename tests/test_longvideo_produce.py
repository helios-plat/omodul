from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from omodul.agentic_longvideo_pipeline import LongVideoResult
from omodul.longvideo_produce import compute_fingerprint_for, longvideo_produce


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
