"""Tests for omodul.undo_changes."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from omodul.undo_changes import Config, InputData, undo_changes


# ---------------------------------------------------------------------------
# 1. Happy path — no lister/restorer, snap_id in result
# ---------------------------------------------------------------------------
async def test_happy_path_returns_completed(tmp_path):
    inp = InputData(snap_id="snap-001", cwd="/project")
    result = await undo_changes(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["snap_id"] == "snap-001"


# ---------------------------------------------------------------------------
# 2. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_lister(**kw):
        await asyncio.sleep(100)

    inp = InputData(snap_id="s1", cwd="/p", snapshot_lister=slow_lister)
    task = asyncio.create_task(undo_changes(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 3. Exception → status="failed"
# ---------------------------------------------------------------------------
async def test_exception_returns_failed(tmp_path):
    def bad_lister(**kw):
        raise OSError("disk error")

    inp = InputData(snap_id="s2", cwd="/p", snapshot_lister=bad_lister)
    result = await undo_changes(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "OSError"


# ---------------------------------------------------------------------------
# 4. Snapshot not found → SnapshotNotFound failed
# ---------------------------------------------------------------------------
async def test_snapshot_not_found(tmp_path):
    lister = AsyncMock(return_value=[{"id": "snap-999"}])
    inp = InputData(snap_id="snap-missing", cwd="/p", snapshot_lister=lister)
    result = await undo_changes(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "SnapshotNotFound"


# ---------------------------------------------------------------------------
# 5. Snapshot found → restorer called
# ---------------------------------------------------------------------------
async def test_restorer_called_when_found(tmp_path):
    lister = AsyncMock(return_value=[{"id": "snap-abc"}])
    restorer = AsyncMock(return_value=None)
    inp = InputData(snap_id="snap-abc", cwd="/proj", snapshot_lister=lister, snapshot_restorer=restorer)
    result = await undo_changes(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    restorer.assert_called_once_with(snap_id="snap-abc", cwd="/proj")


# ---------------------------------------------------------------------------
# 6. No lister — restorer still called
# ---------------------------------------------------------------------------
async def test_no_lister_restorer_called(tmp_path):
    restorer = AsyncMock(return_value=None)
    inp = InputData(snap_id="s3", cwd="/p", snapshot_restorer=restorer)
    result = await undo_changes(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    restorer.assert_called_once()


# ---------------------------------------------------------------------------
# 7. Lister receives cwd
# ---------------------------------------------------------------------------
async def test_lister_called_with_cwd(tmp_path):
    lister = AsyncMock(return_value=[{"id": "s4"}])
    inp = InputData(snap_id="s4", cwd="/my/project", snapshot_lister=lister)
    await undo_changes(Config(), inp, tmp_path)
    lister.assert_called_once_with(cwd="/my/project")


# ---------------------------------------------------------------------------
# 8. snap_id present in result on success
# ---------------------------------------------------------------------------
async def test_snap_id_in_result(tmp_path):
    inp = InputData(snap_id="snap-xyz", cwd="/p")
    result = await undo_changes(Config(), inp, tmp_path)
    assert result["snap_id"] == "snap-xyz"


# ---------------------------------------------------------------------------
# 9. on_step=None works
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    inp = InputData(snap_id="s5", cwd="/p")
    result = await undo_changes(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 10. Required return keys present on success
# ---------------------------------------------------------------------------
async def test_return_keys_present(tmp_path):
    inp = InputData(snap_id="s6", cwd="/p")
    result = await undo_changes(Config(), inp, tmp_path)
    for key in ("status", "error", "snap_id"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 11. Concurrent calls succeed independently
# ---------------------------------------------------------------------------
async def test_concurrent_calls_independent(tmp_path):
    restorer = AsyncMock(return_value=None)

    async def run_one(sid):
        inp = InputData(snap_id=sid, cwd="/p", snapshot_restorer=restorer)
        return await undo_changes(Config(), inp, tmp_path)

    results = await asyncio.gather(run_one("snap-1"), run_one("snap-2"))
    assert all(r["status"] == "completed" for r in results)
    snap_ids = [r["snap_id"] for r in results]
    assert set(snap_ids) == {"snap-1", "snap-2"}


# ---------------------------------------------------------------------------
# 12. Lister returns empty list → SnapshotNotFound
# ---------------------------------------------------------------------------
async def test_lister_empty_list_returns_not_found(tmp_path):
    lister = AsyncMock(return_value=[])
    inp = InputData(snap_id="any", cwd="/p", snapshot_lister=lister)
    result = await undo_changes(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "SnapshotNotFound"


# ---------------------------------------------------------------------------
# 13. Async restorer is awaited
# ---------------------------------------------------------------------------
async def test_async_restorer_awaited(tmp_path):
    called = []

    async def async_restorer(**kw):
        called.append(kw)

    inp = InputData(snap_id="s7", cwd="/p", snapshot_restorer=async_restorer)
    result = await undo_changes(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert len(called) == 1
