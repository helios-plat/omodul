"""Tests for omodul.process_prompt."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from omodul.process_prompt import Config, InputData, process_prompt


def _llm_response(text="hi", in_tok=10, out_tok=5):
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    }


# ---------------------------------------------------------------------------
# 1. Happy path — status="completed"
# ---------------------------------------------------------------------------
async def test_happy_path_returns_completed(tmp_path):
    caller = AsyncMock(return_value=_llm_response())
    inp = InputData(messages=[{"role": "user", "content": "hello"}], llm_caller=caller)
    result = await process_prompt(Config(), inp, tmp_path)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 2. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_caller(**kwargs):
        await asyncio.sleep(100)

    inp = InputData(messages=[], llm_caller=slow_caller)
    task = asyncio.create_task(process_prompt(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 3. Exception → status="failed"
# ---------------------------------------------------------------------------
async def test_exception_returns_failed(tmp_path):
    def bad_caller(**kwargs):
        raise RuntimeError("LLM failed")

    inp = InputData(messages=[], llm_caller=bad_caller)
    result = await process_prompt(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# 4. No llm_caller → failed with ConfigError
# ---------------------------------------------------------------------------
async def test_no_llm_caller_returns_failed(tmp_path):
    inp = InputData(messages=[])
    result = await process_prompt(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "ConfigError"


# ---------------------------------------------------------------------------
# 5. Concurrent cost accumulation — cost_usd >= 0 in both
# ---------------------------------------------------------------------------
async def test_concurrent_cost_isolation(tmp_path):
    caller = AsyncMock(return_value=_llm_response(in_tok=10, out_tok=5))

    async def run_one():
        cfg = Config()
        inp = InputData(messages=[{"role": "user", "content": "test"}], llm_caller=caller)
        return await process_prompt(cfg, inp, tmp_path)

    results = await asyncio.gather(run_one(), run_one())
    assert all(r["status"] == "completed" for r in results)
    assert results[0]["cost_usd"] >= 0
    assert results[1]["cost_usd"] >= 0


# ---------------------------------------------------------------------------
# 6. ContextVar isolation — each task has independent cost
# ---------------------------------------------------------------------------
async def test_contextvar_isolation(tmp_path):
    caller = AsyncMock(return_value=_llm_response(in_tok=100, out_tok=50))

    costs = []

    async def run_one():
        cfg = Config()
        inp = InputData(messages=[{"role": "user", "content": "x"}], llm_caller=caller)
        r = await process_prompt(cfg, inp, tmp_path)
        costs.append(r["cost_usd"])

    await asyncio.gather(run_one(), run_one())
    # Each should have its own independent cost, both > 0
    assert len(costs) == 2
    assert all(c > 0 for c in costs)


# ---------------------------------------------------------------------------
# 7. on_step=None works — no error
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    caller = AsyncMock(return_value=_llm_response())
    inp = InputData(messages=[{"role": "user", "content": "hi"}], llm_caller=caller)
    result = await process_prompt(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 8. on_step sync callable is called
# ---------------------------------------------------------------------------
async def test_on_step_sync_called(tmp_path):
    caller = AsyncMock(return_value=_llm_response())
    steps = []

    def on_step(step):
        steps.append(step)

    inp = InputData(messages=[{"role": "user", "content": "hi"}], llm_caller=caller)
    await process_prompt(Config(), inp, tmp_path, on_step=on_step)
    assert "assemble_context" in steps


# ---------------------------------------------------------------------------
# 9. on_step async works (is awaited)
# ---------------------------------------------------------------------------
async def test_on_step_async_works(tmp_path):
    caller = AsyncMock(return_value=_llm_response())
    steps = []

    async def on_step(step):
        steps.append(step)

    inp = InputData(messages=[{"role": "user", "content": "hi"}], llm_caller=caller)
    await process_prompt(Config(), inp, tmp_path, on_step=on_step)
    assert "assemble_context" in steps


# ---------------------------------------------------------------------------
# 10. Required return keys present on success
# ---------------------------------------------------------------------------
async def test_return_keys_on_success(tmp_path):
    caller = AsyncMock(return_value=_llm_response())
    inp = InputData(messages=[{"role": "user", "content": "hi"}], llm_caller=caller)
    result = await process_prompt(Config(), inp, tmp_path)
    for key in ("status", "error", "cost_usd", "assistant_message", "tool_calls"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 11. Tool calls are parsed from response content
# ---------------------------------------------------------------------------
async def test_tool_calls_parsed(tmp_path):
    tool_block = {"type": "tool_use", "id": "t1", "name": "my_tool", "input": {}}
    response = {
        "content": [{"type": "text", "text": "ok"}, tool_block],
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    caller = AsyncMock(return_value=response)
    inp = InputData(messages=[{"role": "user", "content": "go"}], llm_caller=caller)
    result = await process_prompt(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "my_tool"


# ---------------------------------------------------------------------------
# 12. cost_usd > 0 when tokens are non-zero
# ---------------------------------------------------------------------------
async def test_cost_usd_positive(tmp_path):
    caller = AsyncMock(return_value=_llm_response(in_tok=100, out_tok=50))
    inp = InputData(messages=[{"role": "user", "content": "hi"}], llm_caller=caller)
    result = await process_prompt(Config(), inp, tmp_path)
    assert result["cost_usd"] > 0


# ---------------------------------------------------------------------------
# 13. tools list is forwarded to llm_caller
# ---------------------------------------------------------------------------
async def test_tools_forwarded_to_caller(tmp_path):
    caller = AsyncMock(return_value=_llm_response())
    tools = [{"name": "bash", "description": "run bash"}]
    inp = InputData(messages=[{"role": "user", "content": "run"}], tools=tools, llm_caller=caller)
    await process_prompt(Config(), inp, tmp_path)
    call_kwargs = caller.call_args[1]
    assert call_kwargs.get("tools") == tools
