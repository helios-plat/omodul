"""Tests for omodul.run_subagent_task."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from omodul.run_subagent_task import Config, InputData, run_subagent_task
from omodul._base import CostTracker


# ---------------------------------------------------------------------------
# 1. Happy path — returns completed with plan
# ---------------------------------------------------------------------------
async def test_happy_path_returns_completed(tmp_path):
    inp = InputData(task_description="write tests")
    result = await run_subagent_task(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert "plan" in result


# ---------------------------------------------------------------------------
# 2. Plan contains task description
# ---------------------------------------------------------------------------
async def test_plan_contains_task(tmp_path):
    inp = InputData(task_description="refactor module X")
    result = await run_subagent_task(Config(), inp, tmp_path)
    assert result["plan"]["task"] == "refactor module X"


# ---------------------------------------------------------------------------
# 3. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_dispatcher(**kw):
        await asyncio.sleep(100)

    inp = InputData(task_description="slow", dispatcher=slow_dispatcher)
    task = asyncio.create_task(run_subagent_task(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 4. Exception → status="failed"
# ---------------------------------------------------------------------------
async def test_exception_returns_failed(tmp_path):
    def bad_dispatcher(**kw):
        raise RuntimeError("dispatch error")

    inp = InputData(task_description="x", dispatcher=bad_dispatcher)
    result = await run_subagent_task(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# 5. depth >= max_depth → RecursionLimit error
# ---------------------------------------------------------------------------
async def test_max_depth_exceeded_returns_failed(tmp_path):
    cfg = Config(max_depth=3)
    inp = InputData(task_description="deep", depth=3)
    result = await run_subagent_task(cfg, inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "RecursionLimit"


# ---------------------------------------------------------------------------
# 6. depth < max_depth → succeeds
# ---------------------------------------------------------------------------
async def test_depth_below_max_succeeds(tmp_path):
    cfg = Config(max_depth=3)
    inp = InputData(task_description="shallow", depth=2)
    result = await run_subagent_task(cfg, inp, tmp_path)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 7. Dispatcher result replaces default plan
# ---------------------------------------------------------------------------
async def test_dispatcher_replaces_plan(tmp_path):
    custom_plan = {"task": "custom", "steps": ["a", "b"]}
    dispatcher = AsyncMock(return_value=custom_plan)
    inp = InputData(task_description="original", dispatcher=dispatcher)
    result = await run_subagent_task(Config(), inp, tmp_path)
    assert result["plan"] == custom_plan


# ---------------------------------------------------------------------------
# 8. Dispatcher returning falsy → default plan used
# ---------------------------------------------------------------------------
async def test_dispatcher_falsy_uses_default_plan(tmp_path):
    dispatcher = AsyncMock(return_value=None)
    inp = InputData(task_description="my task", dispatcher=dispatcher)
    result = await run_subagent_task(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["plan"]["task"] == "my task"


# ---------------------------------------------------------------------------
# 9. concurrent cost accumulation — cost_usd >= 0
# ---------------------------------------------------------------------------
async def test_concurrent_cost_isolation(tmp_path):
    async def run_one():
        inp = InputData(task_description="t")
        return await run_subagent_task(Config(), inp, tmp_path)

    results = await asyncio.gather(run_one(), run_one())
    assert all(r["status"] == "completed" for r in results)
    assert all(r["cost_usd"] >= 0 for r in results)


# ---------------------------------------------------------------------------
# 10. ContextVar isolation — parent_cost_tracker shared across calls
# ---------------------------------------------------------------------------
async def test_parent_cost_tracker_shared(tmp_path):
    shared_cost = CostTracker()
    inp = InputData(task_description="t", parent_cost_tracker=shared_cost)
    result = await run_subagent_task(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    # cost_usd in result reflects the shared tracker (starts at 0, no LLM calls here)
    assert result["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# 11. on_step=None works
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    inp = InputData(task_description="t")
    result = await run_subagent_task(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 12. Required return keys present
# ---------------------------------------------------------------------------
async def test_return_keys_present(tmp_path):
    inp = InputData(task_description="t")
    result = await run_subagent_task(Config(), inp, tmp_path)
    for key in ("status", "error", "cost_usd", "plan"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 13. allowed_tools from config included in default plan
# ---------------------------------------------------------------------------
async def test_allowed_tools_in_plan(tmp_path):
    cfg = Config(allowed_tools=["bash", "read"])
    inp = InputData(task_description="t")
    result = await run_subagent_task(cfg, inp, tmp_path)
    assert result["plan"]["allowed_tools"] == ["bash", "read"]
