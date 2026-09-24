from pathlib import Path

import pytest

from omodul.presenter_video_produce import compute_fingerprint_for, presenter_video_produce


@pytest.mark.asyncio
async def test_presenter_video_produce_returns_only_existing_video(tmp_path: Path) -> None:
    async def renderer(_presentation: dict, output_dir: Path, _config: dict) -> dict:
        video = output_dir / "final.mp4"
        video.write_bytes(b"video")
        return {"video_path": video, "report": {"layers": 6}}

    result = await presenter_video_produce(
        {"renderer": "tongjian"},
        {"presentation": {"kind": "history"}, "renderer": renderer},
        tmp_path,
    )

    assert result["status"] == "succeeded"
    assert result["artifacts"][0]["path"] == str(tmp_path / "final.mp4")
    assert result["report"] == {"layers": 6}


@pytest.mark.asyncio
async def test_presenter_video_produce_rejects_missing_final_video(tmp_path: Path) -> None:
    async def renderer(_presentation: dict, _output_dir: Path, _config: dict) -> dict:
        return {"video_path": tmp_path / "missing.mp4"}

    result = await presenter_video_produce(
        {}, {"presentation": {"kind": "history"}, "renderer": renderer}, tmp_path
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "ARTIFACT_MISSING"


def test_presenter_fingerprint_excludes_presentation_content() -> None:
    fingerprint = compute_fingerprint_for(
        {"renderer": "tongjian"},
        {"presentation": {"kind": "history", "script": "private source material"}},
    )

    assert "private" not in fingerprint
    assert len(fingerprint) == 64
