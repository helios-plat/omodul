from pathlib import Path

import pytest

from omodul.narrated_video_produce import compute_fingerprint_for, narrated_video_produce


@pytest.mark.asyncio
async def test_narrated_video_produce_returns_verified_portrait_and_landscape(tmp_path: Path) -> None:
    async def renderer(_storyboard: dict, output_dir: Path, _config: dict) -> dict:
        portrait = output_dir / "portrait.mp4"
        landscape = output_dir / "landscape.mp4"
        portrait.write_bytes(b"portrait")
        landscape.write_bytes(b"landscape")
        return {"portrait_path": portrait, "landscape_path": landscape}

    result = await narrated_video_produce(
        {"renderer": "remotion"},
        {"storyboard": {"segments": [{"narration": "private text"}]}, "renderer": renderer},
        tmp_path,
    )

    assert result["status"] == "succeeded"
    assert [artifact["path"] for artifact in result["artifacts"]] == [
        str(tmp_path / "portrait.mp4"),
        str(tmp_path / "landscape.mp4"),
    ]


@pytest.mark.asyncio
async def test_narrated_video_produce_reports_missing_artifact_without_success(tmp_path: Path) -> None:
    async def renderer(_storyboard: dict, _output_dir: Path, _config: dict) -> dict:
        return {"portrait_path": tmp_path / "missing.mp4"}

    result = await narrated_video_produce(
        {}, {"storyboard": {"segments": []}, "renderer": renderer}, tmp_path
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "ARTIFACT_MISSING"


def test_narrated_fingerprint_excludes_storyboard_content() -> None:
    fingerprint = compute_fingerprint_for(
        {"renderer": "remotion"},
        {"storyboard": {"topic": "private topic", "segments": [{"narration": "secret"}]}},
    )

    assert "private" not in fingerprint
    assert "secret" not in fingerprint
    assert len(fingerprint) == 64
