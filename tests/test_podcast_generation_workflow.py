import pytest
from pathlib import Path
from omodul.podcast_generation_workflow import PodcastGenerationConfig, podcast_generation_workflow


@pytest.mark.asyncio
async def test_basic(tmp_path):
    cfg = PodcastGenerationConfig()
    tts_calls = []
    def tts_fn(text, voice, output_path):
        Path(output_path).write_bytes(b"x")
        tts_calls.append(output_path)
        return output_path
    inp = {"content": "hello world foo bar", "tts_fn": tts_fn, "llm_caller": None}
    res = await podcast_generation_workflow(cfg, inp, tmp_path)
    assert res["status"] == "completed"
    assert res["findings"]["script"]
