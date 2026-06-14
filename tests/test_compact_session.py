"""Tests for omodul.compact_session."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from omodul.compact_session import Config, InputData, compact_session, compute_fingerprint_for


# ---------------------------------------------------------------------------
# 1. Happy path — no compactor, history short → compacted=False
# ---------------------------------------------------------------------------
async def test_no_compact_when_below_threshold(tmp_path):
    inp = InputData(session_id="s1", history=[{"role": "user", "content": "hi"}])
    result = await compact_session(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["compacted"] is False


# ---------------------------------------------------------------------------
# 2. Compactor provided → compacted=True
# ---------------------------------------------------------------------------
async def test_compactor_called_returns_compacted(tmp_path):
    compactor = AsyncMock(return_value=[{"role": "summary", "content": "..."}])
    inp = InputData(session_id="s2", history=[{"role": "user", "content": "x"}], compactor=compactor)
    result = await compact_session(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["compacted"] is True
    compactor.assert_called_once()


# ---------------------------------------------------------------------------
# 3. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_compactor(**kw):
        await asyncio.sleep(100)

    inp = InputData(session_id="s3", history=[{}], compactor=slow_compactor)
    task = asyncio.create_task(compact_session(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 4. Exception → status="failed"
# ---------------------------------------------------------------------------
async def test_exception_returns_failed(tmp_path):
    def bad_compactor(**kw):
        raise RuntimeError("compactor error")

    inp = InputData(session_id="s4", history=[{}], compactor=bad_compactor)
    result = await compact_session(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# 5. compute_fingerprint_for is deterministic
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_deterministic():
    cfg = Config()
    inp = InputData(session_id="abc", history=[{"a": 1}, {"b": 2}])
    fp1 = compute_fingerprint_for(cfg, inp)
    fp2 = compute_fingerprint_for(cfg, inp)
    assert fp1 == fp2


# ---------------------------------------------------------------------------
# 6. compute_fingerprint_for changes with different session_id
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_changes_with_session_id():
    cfg = Config()
    inp1 = InputData(session_id="aaa", history=[])
    inp2 = InputData(session_id="bbb", history=[])
    assert compute_fingerprint_for(cfg, inp1) != compute_fingerprint_for(cfg, inp2)


# ---------------------------------------------------------------------------
# 7. compute_fingerprint_for changes with different history length
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_changes_with_history_len():
    cfg = Config()
    inp1 = InputData(session_id="s", history=[])
    inp2 = InputData(session_id="s", history=[{"x": 1}])
    assert compute_fingerprint_for(cfg, inp1) != compute_fingerprint_for(cfg, inp2)


# ---------------------------------------------------------------------------
# 8. fingerprint present in result
# ---------------------------------------------------------------------------
async def test_fingerprint_in_result(tmp_path):
    inp = InputData(session_id="s5", history=[])
    result = await compact_session(Config(), inp, tmp_path)
    assert "fingerprint" in result
    assert isinstance(result["fingerprint"], str)
    assert len(result["fingerprint"]) == 24


# ---------------------------------------------------------------------------
# 9. on_step=None works — no error
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    inp = InputData(session_id="s6", history=[])
    result = await compact_session(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 10. Concurrent calls have independent results
# ---------------------------------------------------------------------------
async def test_concurrent_calls_independent(tmp_path):
    compactor = AsyncMock(return_value=[{"role": "summary", "content": "..."}])

    async def run_one(sid):
        inp = InputData(session_id=sid, history=[{"m": i} for i in range(3)], compactor=compactor)
        return await compact_session(Config(), inp, tmp_path)

    results = await asyncio.gather(run_one("s7"), run_one("s8"))
    assert all(r["status"] == "completed" for r in results)
    assert all(r["compacted"] is True for r in results)


# ---------------------------------------------------------------------------
# 11. new_history_len in result when compacted
# ---------------------------------------------------------------------------
async def test_new_history_len_in_result(tmp_path):
    compactor = AsyncMock(return_value=[{"role": "summary", "content": "compact"}])
    inp = InputData(session_id="s9", history=[{"a": 1}, {"b": 2}], compactor=compactor)
    result = await compact_session(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["new_history_len"] == 1


# ---------------------------------------------------------------------------
# 12. Compactor returns None → original history used
# ---------------------------------------------------------------------------
async def test_compactor_returns_none_uses_original(tmp_path):
    compactor = AsyncMock(return_value=None)
    history = [{"x": 1}, {"y": 2}, {"z": 3}]
    inp = InputData(session_id="s10", history=history, compactor=compactor)
    result = await compact_session(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["new_history_len"] == 3


# ---------------------------------------------------------------------------
# 13. Required return keys present
# ---------------------------------------------------------------------------
async def test_return_keys_present(tmp_path):
    inp = InputData(session_id="s11", history=[])
    result = await compact_session(Config(), inp, tmp_path)
    for key in ("status", "error", "fingerprint", "compacted"):
        assert key in result, f"missing key: {key}"
