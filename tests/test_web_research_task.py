"""Tests for omodul.web_research_task."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from omodul.web_research_task import Config, InputData, web_research_task


def _llm_response(text="Summary here"):
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 30, "output_tokens": 15},
    }


def _research_result(snippets=None):
    return {"snippets": snippets or ["snippet 1", "snippet 2"], "urls": ["https://example.com"]}


# ---------------------------------------------------------------------------
# 1. Happy path — researcher returns snippets, no LLM
# ---------------------------------------------------------------------------
async def test_happy_path_no_llm(tmp_path):
    researcher = AsyncMock(return_value=_research_result())
    inp = InputData(query="python asyncio", researcher=researcher)
    result = await web_research_task(Config(), inp, tmp_path)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 2. report_path written to disk
# ---------------------------------------------------------------------------
async def test_report_path_written(tmp_path):
    researcher = AsyncMock(return_value=_research_result())
    inp = InputData(query="test query", researcher=researcher)
    result = await web_research_task(Config(), inp, tmp_path)
    assert Path(result["report_path"]).exists()


# ---------------------------------------------------------------------------
# 3. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_researcher(**kw):
        await asyncio.sleep(100)

    inp = InputData(query="q", researcher=slow_researcher)
    task = asyncio.create_task(web_research_task(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 4. Exception → status="failed"
# ---------------------------------------------------------------------------
async def test_exception_returns_failed(tmp_path):
    def bad_researcher(**kw):
        raise ConnectionError("network error")

    inp = InputData(query="q", researcher=bad_researcher)
    result = await web_research_task(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "ConnectionError"


# ---------------------------------------------------------------------------
# 5. No researcher and no snippets → NoResults failed
# ---------------------------------------------------------------------------
async def test_no_researcher_returns_no_results(tmp_path):
    inp = InputData(query="anything")
    result = await web_research_task(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "NoResults"


# ---------------------------------------------------------------------------
# 6. Researcher returns empty snippets → NoResults failed
# ---------------------------------------------------------------------------
async def test_empty_snippets_returns_no_results(tmp_path):
    researcher = AsyncMock(return_value={"snippets": [], "urls": []})
    inp = InputData(query="q", researcher=researcher)
    result = await web_research_task(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "NoResults"


# ---------------------------------------------------------------------------
# 7. LLM caller synthesises snippets, cost_usd > 0
# ---------------------------------------------------------------------------
async def test_llm_synthesises_and_cost_positive(tmp_path):
    researcher = AsyncMock(return_value=_research_result())
    llm_caller = AsyncMock(return_value=_llm_response())
    inp = InputData(query="q", researcher=researcher, llm_caller=llm_caller)
    result = await web_research_task(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["cost_usd"] > 0


# ---------------------------------------------------------------------------
# 8. concurrent cost isolation
# ---------------------------------------------------------------------------
async def test_concurrent_cost_isolation(tmp_path):
    researcher = AsyncMock(return_value=_research_result())
    llm_caller = AsyncMock(return_value=_llm_response())

    async def run_one():
        inp = InputData(query="q", researcher=researcher, llm_caller=llm_caller)
        return await web_research_task(Config(), inp, tmp_path)

    results = await asyncio.gather(run_one(), run_one())
    assert all(r["status"] == "completed" for r in results)
    assert all(r["cost_usd"] >= 0 for r in results)


# ---------------------------------------------------------------------------
# 9. on_step=None works
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    researcher = AsyncMock(return_value=_research_result())
    inp = InputData(query="q", researcher=researcher)
    result = await web_research_task(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 10. Required return keys present on success
# ---------------------------------------------------------------------------
async def test_return_keys_on_success(tmp_path):
    researcher = AsyncMock(return_value=_research_result())
    inp = InputData(query="q", researcher=researcher)
    result = await web_research_task(Config(), inp, tmp_path)
    for key in ("status", "error", "cost_usd", "report_path"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 11. researcher called with query and max_pages
# ---------------------------------------------------------------------------
async def test_researcher_called_with_query_and_max_pages(tmp_path):
    researcher = AsyncMock(return_value=_research_result())
    cfg = Config(max_pages=3)
    inp = InputData(query="my query", researcher=researcher)
    await web_research_task(cfg, inp, tmp_path)
    kwargs = researcher.call_args[1]
    assert kwargs["query"] == "my query"
    assert kwargs["max_pages"] == 3


# ---------------------------------------------------------------------------
# 12. LLM caller receives joined snippets
# ---------------------------------------------------------------------------
async def test_llm_receives_snippets(tmp_path):
    researcher = AsyncMock(return_value=_research_result(["s1", "s2"]))
    llm_caller = AsyncMock(return_value=_llm_response())
    inp = InputData(query="q", researcher=researcher, llm_caller=llm_caller)
    await web_research_task(Config(), inp, tmp_path)
    llm_kwargs = llm_caller.call_args[1]
    content = llm_kwargs["messages"][0]["content"]
    assert "s1" in content
    assert "s2" in content


# ---------------------------------------------------------------------------
# 13. Report file contains content (not empty)
# ---------------------------------------------------------------------------
async def test_report_file_not_empty(tmp_path):
    researcher = AsyncMock(return_value=_research_result(["important finding"]))
    inp = InputData(query="q", researcher=researcher)
    result = await web_research_task(Config(), inp, tmp_path)
    content = Path(result["report_path"]).read_text()
    assert len(content) > 0
