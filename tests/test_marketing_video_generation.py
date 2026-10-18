import pytest
from pathlib import Path
from omodul.marketing_video_generation import MarketingVideoConfig, marketing_video_generation


@pytest.mark.asyncio
async def test_basic(tmp_path):
    cfg = MarketingVideoConfig()
    tpl = {"scenes": [{"id": 1, "duration": 2, "text": "hi {{name}}", "asset_key": "img"}]}
    inp = {"template": tpl, "assets": {"img": "X"}, "variables": {"name": "Bob"}, "subtitles": [{"start": 0, "end": 1, "text": "t"}]}
    res = await marketing_video_generation(cfg, inp, tmp_path)
    assert res["status"] == "completed"
    assert res["findings"]["scene_count"] == 1
