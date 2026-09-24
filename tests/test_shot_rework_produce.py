from pathlib import Path

import pytest

from omodul.shot_rework_produce import shot_rework_produce


@pytest.mark.asyncio
async def test_shot_rework_produce_returns_a_verified_video(tmp_path: Path) -> None:
    async def renderer(
        output_dir: Path, _config: dict, shot_ids: list[int], hints: dict[int, str]
    ) -> dict:
        assert shot_ids == [2]
        assert hints == {2: "brighter lighting"}
        video = output_dir / "final.mp4"
        video.write_bytes(b"video")
        return {"url": str(video), "shots": [{"index": 2}]}

    result = await shot_rework_produce(
        {"video_provider": "fake"},
        {"shot_ids": [2], "hints": {2: "brighter lighting"}, "renderer": renderer},
        tmp_path,
    )

    assert result["status"] == "succeeded"
    assert result["artifacts"][0]["path"] == str(tmp_path / "final.mp4")
    assert result["report"]["reworked_shot_count"] == 1


@pytest.mark.asyncio
async def test_shot_rework_produce_rejects_missing_artifacts(tmp_path: Path) -> None:
    result = await shot_rework_produce(
        {"video_provider": "fake"},
        {"shot_ids": [0], "renderer": lambda *_args: {"url": str(tmp_path / "missing.mp4")}},
        tmp_path,
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "ARTIFACT_MISSING"
