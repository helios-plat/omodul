"""Tests for omodul.execute_tool."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from omodul.execute_tool import Config, InputData, execute_tool


# ---------------------------------------------------------------------------
# 1. Happy path — tool found and executed
# ---------------------------------------------------------------------------
async def test_happy_path_returns_completed(tmp_path):
    registry = {"echo": lambda **kw: "pong"}
    inp = InputData(tool_name="echo", tool_registry=registry)
    result = await execute_tool(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["tool_result"] == "pong"


# ---------------------------------------------------------------------------
# 2. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_tool(**kw):
        await asyncio.sleep(100)

    inp = InputData(tool_name="slow", tool_registry={"slow": slow_tool})
    task = asyncio.create_task(execute_tool(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 3. Exception → status="failed"
# ---------------------------------------------------------------------------
async def test_exception_returns_failed(tmp_path):
    def bad_tool(**kw):
        raise ValueError("boom")

    inp = InputData(tool_name="bad", tool_registry={"bad": bad_tool})
    result = await execute_tool(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "ValueError"


# ---------------------------------------------------------------------------
# 4. Tool not in registry → failed ToolNotFound
# ---------------------------------------------------------------------------
async def test_tool_not_found(tmp_path):
    inp = InputData(tool_name="missing", tool_registry={})
    result = await execute_tool(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "ToolNotFound"


# ---------------------------------------------------------------------------
# 5. Permission checker returns "deny" → failed PermissionDenied
# ---------------------------------------------------------------------------
async def test_permission_deny(tmp_path):
    checker = AsyncMock(return_value="deny")
    registry = {"rm": lambda **kw: "deleted"}
    inp = InputData(tool_name="rm", tool_registry=registry, permission_checker=checker)
    result = await execute_tool(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "PermissionDenied"


# ---------------------------------------------------------------------------
# 6. Permission checker returns "ask" → needs_confirmation=True
# ---------------------------------------------------------------------------
async def test_permission_ask(tmp_path):
    checker = AsyncMock(return_value="ask")
    registry = {"rm": lambda **kw: "deleted"}
    inp = InputData(tool_name="rm", tool_registry=registry, permission_checker=checker)
    result = await execute_tool(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result.get("needs_confirmation") is True


# ---------------------------------------------------------------------------
# 7. Permission checker returns "allow" → tool executes
# ---------------------------------------------------------------------------
async def test_permission_allow(tmp_path):
    checker = AsyncMock(return_value="allow")
    registry = {"safe": lambda **kw: "ok"}
    inp = InputData(tool_name="safe", tool_registry=registry, permission_checker=checker)
    result = await execute_tool(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["tool_result"] == "ok"


# ---------------------------------------------------------------------------
# 8. Async tool function is awaited
# ---------------------------------------------------------------------------
async def test_async_tool_awaited(tmp_path):
    async def async_tool(**kw):
        return "async_result"

    inp = InputData(tool_name="async_t", tool_registry={"async_t": async_tool})
    result = await execute_tool(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["tool_result"] == "async_result"


# ---------------------------------------------------------------------------
# 9. Tool returns dict with output/exit_code
# ---------------------------------------------------------------------------
async def test_tool_returns_dict_with_output(tmp_path):
    registry = {"cmd": lambda **kw: {"output": "hello world", "exit_code": 0}}
    inp = InputData(tool_name="cmd", tool_registry=registry)
    result = await execute_tool(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["tool_result"] == "hello world"
    assert result["exit_code"] == 0


# ---------------------------------------------------------------------------
# 10. Required return keys present on success
# ---------------------------------------------------------------------------
async def test_return_keys_on_success(tmp_path):
    registry = {"t": lambda **kw: 42}
    inp = InputData(tool_name="t", tool_registry=registry)
    result = await execute_tool(Config(), inp, tmp_path)
    for key in ("status", "error", "tool_result", "exit_code"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 11. on_step=None works — no error
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    registry = {"t": lambda **kw: "x"}
    inp = InputData(tool_name="t", tool_registry=registry)
    result = await execute_tool(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 12. Concurrent executions succeed independently
# ---------------------------------------------------------------------------
async def test_concurrent_executions(tmp_path):
    call_count = 0

    def counter_tool(**kw):
        nonlocal call_count
        call_count += 1
        return call_count

    registry = {"counter": counter_tool}

    async def run_one():
        inp = InputData(tool_name="counter", tool_registry=registry)
        return await execute_tool(Config(), inp, tmp_path)

    results = await asyncio.gather(run_one(), run_one(), run_one())
    assert all(r["status"] == "completed" for r in results)
    assert call_count == 3


# ---------------------------------------------------------------------------
# 13. No permission checker — tool executes directly
# ---------------------------------------------------------------------------
async def test_no_permission_checker_executes_directly(tmp_path):
    registry = {"t": lambda **kw: "direct"}
    inp = InputData(tool_name="t", tool_registry=registry, permission_checker=None)
    result = await execute_tool(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["tool_result"] == "direct"
