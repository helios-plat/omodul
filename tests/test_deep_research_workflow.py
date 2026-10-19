import pytest
from pathlib import Path
from omodul.deep_research_workflow import DeepResearchConfig, deep_research_workflow


@pytest.mark.asyncio
async def test_basic(tmp_path):
    cfg = DeepResearchConfig(max_iterations=2)
    inp = {"query": "what is AI?", "search_fn": lambda q: ["r1"], "synthesize_fn": lambda q, r: "ans"}
    res = await deep_research_workflow(cfg, inp, tmp_path)
    assert res["status"] == "completed"
    assert res["findings"]["subqueries"]
    assert res["report_path"] is not None


@pytest.mark.asyncio
async def test_failure(tmp_path):
    cfg = DeepResearchConfig()
    # force error by bad search_fn? still handled
    res = await deep_research_workflow(cfg, {"query": "q"}, tmp_path)
    assert res["status"] == "completed"
