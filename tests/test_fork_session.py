"""Tests for omodul.fork_session."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from omodul.fork_session import Config, InputData, fork_session, compute_fingerprint_for


# ---------------------------------------------------------------------------
# 1. Happy path — no loader, uses history_slice
# ---------------------------------------------------------------------------
async def test_happy_path_returns_completed(tmp_path):
    inp = InputData(source_session_id="src-1", history_slice=[{"role": "user", "content": "hi"}])
    result = await fork_session(Config(), inp, tmp_path)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 2. New session_id is a UUID different from source
# ---------------------------------------------------------------------------
async def test_new_session_id_is_uuid(tmp_path):
    inp = InputData(source_session_id="src-2", history_slice=[])
    result = await fork_session(Config(), inp, tmp_path)
    new_id = result["session_id"]
    uuid.UUID(new_id)  # raises if invalid
    assert new_id != "src-2"


# ---------------------------------------------------------------------------
# 3. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_loader(**kw):
        await asyncio.sleep(100)

    inp = InputData(source_session_id="src-3", loader=slow_loader)
    task = asyncio.create_task(fork_session(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 4. Exception → status="failed"
# ---------------------------------------------------------------------------
async def test_exception_returns_failed(tmp_path):
    def bad_loader(**kw):
        raise ValueError("load failed")

    inp = InputData(source_session_id="src-4", loader=bad_loader)
    result = await fork_session(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "ValueError"


# ---------------------------------------------------------------------------
# 5. Loader is called with source_session_id
# ---------------------------------------------------------------------------
async def test_loader_called_with_source_id(tmp_path):
    loader = AsyncMock(return_value={"id": "src-5", "history": [{"m": 1}]})
    inp = InputData(source_session_id="src-5", loader=loader)
    await fork_session(Config(), inp, tmp_path)
    loader.assert_called_once()
    assert loader.call_args[1]["session_id"] == "src-5"


# ---------------------------------------------------------------------------
# 6. history_slice overrides loader history
# ---------------------------------------------------------------------------
async def test_history_slice_overrides_loader(tmp_path):
    loader = AsyncMock(return_value={"id": "src-6", "history": [{"orig": True}]})
    history_slice = [{"sliced": True}]
    inp = InputData(source_session_id="src-6", loader=loader, history_slice=history_slice)
    result = await fork_session(Config(), inp, tmp_path)
    assert result["forked_session"]["history"] == history_slice


# ---------------------------------------------------------------------------
# 7. forked_session has parent_id == source_session_id
# ---------------------------------------------------------------------------
async def test_forked_session_has_parent_id(tmp_path):
    inp = InputData(source_session_id="src-7", history_slice=[])
    result = await fork_session(Config(), inp, tmp_path)
    assert result["forked_session"]["parent_id"] == "src-7"


# ---------------------------------------------------------------------------
# 8. compute_fingerprint_for is deterministic
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_deterministic():
    cfg = Config()
    inp = InputData(source_session_id="sid-abc")
    fp1 = compute_fingerprint_for(cfg, inp)
    fp2 = compute_fingerprint_for(cfg, inp)
    assert fp1 == fp2


# ---------------------------------------------------------------------------
# 9. compute_fingerprint_for changes with different source_session_id
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_changes_with_source_id():
    cfg = Config()
    fp1 = compute_fingerprint_for(cfg, InputData(source_session_id="A"))
    fp2 = compute_fingerprint_for(cfg, InputData(source_session_id="B"))
    assert fp1 != fp2


# ---------------------------------------------------------------------------
# 10. fingerprint in result
# ---------------------------------------------------------------------------
async def test_fingerprint_in_result(tmp_path):
    inp = InputData(source_session_id="src-8", history_slice=[])
    result = await fork_session(Config(), inp, tmp_path)
    assert "fingerprint" in result
    assert len(result["fingerprint"]) == 24


# ---------------------------------------------------------------------------
# 11. on_step=None works
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    inp = InputData(source_session_id="src-9", history_slice=[])
    result = await fork_session(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 12. Required return keys present
# ---------------------------------------------------------------------------
async def test_return_keys_present(tmp_path):
    inp = InputData(source_session_id="src-10", history_slice=[])
    result = await fork_session(Config(), inp, tmp_path)
    for key in ("status", "error", "fingerprint", "session_id", "forked_session"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 13. Concurrent forks produce distinct new IDs
# ---------------------------------------------------------------------------
async def test_concurrent_forks_distinct_ids(tmp_path):
    async def run_one():
        inp = InputData(source_session_id="src-shared", history_slice=[])
        return await fork_session(Config(), inp, tmp_path)

    results = await asyncio.gather(run_one(), run_one(), run_one())
    assert all(r["status"] == "completed" for r in results)
    ids = [r["session_id"] for r in results]
    assert len(set(ids)) == 3
