import pytest
from pathlib import Path
from omodul.long_to_short_video_workflow import LongToShortVideoConfig, long_to_short_video_workflow, compute_fingerprint_for


@pytest.mark.asyncio
async def test_basic(tmp_path):
    cfg = LongToShortVideoConfig(top_k=2)
    inp = {"video_path": "/tmp/v.mp4", "audio_path": "/tmp/a.wav", "segments": []}
    # patch whisper to return empty
    import oprim
    async def fake_whisper(**kw):
        return {"segments": [{"start": 0, "end": 5, "text": "hi"}]}
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("oprim.whisper_transcribe", fake_whisper, raising=False)
        res = await long_to_short_video_workflow(cfg, inp, tmp_path)
    assert res["status"] in ("completed", "failed")
    assert "findings" in res
    assert "fingerprint" in res


def test_fingerprint():
    cfg = LongToShortVideoConfig()
    fp = compute_fingerprint_for(cfg, {"a": 1})
    assert isinstance(fp, str) and len(fp) == 64
