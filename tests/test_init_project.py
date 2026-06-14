"""Tests for omodul.init_project."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from omodul.init_project import Config, InputData, init_project


def _llm_response(text="# AGENTS\n\nHello"):
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 20, "output_tokens": 10},
    }


# ---------------------------------------------------------------------------
# 1. Happy path with scan_fn and llm_caller
# ---------------------------------------------------------------------------
async def test_happy_path_returns_completed(tmp_path):
    scan_fn = AsyncMock(return_value={"files": ["main.py", "README.md"], "root": str(tmp_path)})
    llm_caller = AsyncMock(return_value=_llm_response())
    inp = InputData(root_path=str(tmp_path), scan_fn=scan_fn, llm_caller=llm_caller)
    result = await init_project(Config(), inp, tmp_path)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 2. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_caller(**kw):
        await asyncio.sleep(100)

    scan_fn = AsyncMock(return_value={"files": [], "root": str(tmp_path)})
    inp = InputData(root_path=str(tmp_path), scan_fn=scan_fn, llm_caller=slow_caller)
    task = asyncio.create_task(init_project(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 3. Exception → status="failed"
# ---------------------------------------------------------------------------
async def test_exception_returns_failed(tmp_path):
    def bad_scan(**kw):
        raise RuntimeError("scan failed")

    inp = InputData(root_path=str(tmp_path), scan_fn=bad_scan)
    result = await init_project(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# 4. No scan_fn — fallback uses rglob on root_path
# ---------------------------------------------------------------------------
async def test_no_scan_fn_uses_fallback(tmp_path):
    (tmp_path / "app.py").write_text("x = 1")
    inp = InputData(root_path=str(tmp_path))
    result = await init_project(Config(), inp, tmp_path)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 5. report_path returned and file written
# ---------------------------------------------------------------------------
async def test_report_path_written(tmp_path):
    scan_fn = AsyncMock(return_value={"files": ["a.py"], "root": str(tmp_path)})
    llm_caller = AsyncMock(return_value=_llm_response("# AGENTS\n\nContent"))
    inp = InputData(root_path=str(tmp_path), scan_fn=scan_fn, llm_caller=llm_caller)
    result = await init_project(Config(), inp, tmp_path)
    assert "report_path" in result
    assert Path(result["report_path"]).exists()


# ---------------------------------------------------------------------------
# 6. cost_usd > 0 when llm_caller returns tokens
# ---------------------------------------------------------------------------
async def test_cost_usd_positive_with_llm(tmp_path):
    scan_fn = AsyncMock(return_value={"files": [], "root": str(tmp_path)})
    llm_caller = AsyncMock(return_value=_llm_response())
    inp = InputData(root_path=str(tmp_path), scan_fn=scan_fn, llm_caller=llm_caller)
    result = await init_project(Config(), inp, tmp_path)
    assert result["cost_usd"] > 0


# ---------------------------------------------------------------------------
# 7. Concurrent cost isolation
# ---------------------------------------------------------------------------
async def test_concurrent_cost_isolation(tmp_path):
    scan_fn = AsyncMock(return_value={"files": [], "root": str(tmp_path)})
    llm_caller = AsyncMock(return_value=_llm_response())

    async def run_one():
        inp = InputData(root_path=str(tmp_path), scan_fn=scan_fn, llm_caller=llm_caller)
        return await init_project(Config(), inp, tmp_path)

    results = await asyncio.gather(run_one(), run_one())
    assert all(r["status"] == "completed" for r in results)
    assert all(r["cost_usd"] >= 0 for r in results)


# ---------------------------------------------------------------------------
# 8. on_step=None works
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    scan_fn = AsyncMock(return_value={"files": [], "root": str(tmp_path)})
    inp = InputData(root_path=str(tmp_path), scan_fn=scan_fn)
    result = await init_project(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 9. No llm_caller → fallback AGENTS.md content written
# ---------------------------------------------------------------------------
async def test_no_llm_caller_writes_fallback(tmp_path):
    scan_fn = AsyncMock(return_value={"files": ["x.py"], "root": str(tmp_path)})
    inp = InputData(root_path=str(tmp_path), scan_fn=scan_fn, llm_caller=None)
    result = await init_project(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    report = Path(result["report_path"])
    assert report.exists()
    assert "AGENTS" in report.read_text()


# ---------------------------------------------------------------------------
# 10. Required return keys present
# ---------------------------------------------------------------------------
async def test_return_keys_present(tmp_path):
    scan_fn = AsyncMock(return_value={"files": [], "root": str(tmp_path)})
    inp = InputData(root_path=str(tmp_path), scan_fn=scan_fn)
    result = await init_project(Config(), inp, tmp_path)
    for key in ("status", "error", "cost_usd", "report_path"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 11. max_files config respected in fallback scan
# ---------------------------------------------------------------------------
async def test_max_files_limits_scan(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("x")
    cfg = Config(max_files=3)
    inp = InputData(root_path=str(tmp_path))
    result = await init_project(cfg, inp, tmp_path)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 12. scan_fn receives root as Path
# ---------------------------------------------------------------------------
async def test_scan_fn_called_with_root(tmp_path):
    scan_fn = AsyncMock(return_value={"files": [], "root": str(tmp_path)})
    inp = InputData(root_path=str(tmp_path), scan_fn=scan_fn)
    await init_project(Config(), inp, tmp_path)
    scan_fn.assert_called_once()
    kwargs = scan_fn.call_args[1]
    assert "root" in kwargs


# ---------------------------------------------------------------------------
# 13. LLM caller receives files list in message
# ---------------------------------------------------------------------------
async def test_llm_caller_receives_files(tmp_path):
    scan_fn = AsyncMock(return_value={"files": ["alpha.py", "beta.py"], "root": str(tmp_path)})
    llm_caller = AsyncMock(return_value=_llm_response())
    inp = InputData(root_path=str(tmp_path), scan_fn=scan_fn, llm_caller=llm_caller)
    await init_project(Config(), inp, tmp_path)
    llm_caller.assert_called_once()
    kwargs = llm_caller.call_args[1]
    content = kwargs["messages"][0]["content"]
    assert "alpha.py" in content
    assert "beta.py" in content
