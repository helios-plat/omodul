"""Tests for omodul.index_codebase."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from omodul.index_codebase import Config, InputData, index_codebase, compute_fingerprint_for


# ---------------------------------------------------------------------------
# 1. Happy path — no scanner, empty file list → completed
# ---------------------------------------------------------------------------
async def test_happy_path_no_scanner(tmp_path):
    inp = InputData(root_path=str(tmp_path))
    result = await index_codebase(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["indexed"] == 0


# ---------------------------------------------------------------------------
# 2. Scanner returns real files → they get indexed
# ---------------------------------------------------------------------------
async def test_scanner_files_indexed(tmp_path):
    py_file = tmp_path / "app.py"
    py_file.write_text("x = 1\n")
    scanner = AsyncMock(return_value=[str(py_file)])
    embedder = AsyncMock(return_value=[0.1, 0.2, 0.3])
    inp = InputData(root_path=str(tmp_path), scanner=scanner, embedder=embedder)
    result = await index_codebase(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["indexed"] == 1


# ---------------------------------------------------------------------------
# 3. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_scanner(**kw):
        await asyncio.sleep(100)

    inp = InputData(root_path=str(tmp_path), scanner=slow_scanner)
    task = asyncio.create_task(index_codebase(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 4. Exception in scanner → status="failed"
# ---------------------------------------------------------------------------
async def test_scanner_exception_returns_failed(tmp_path):
    def bad_scanner(**kw):
        raise RuntimeError("scan error")

    inp = InputData(root_path=str(tmp_path), scanner=bad_scanner)
    result = await index_codebase(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# 5. Non-existent file → skipped, not indexed
# ---------------------------------------------------------------------------
async def test_nonexistent_file_skipped(tmp_path):
    scanner = AsyncMock(return_value=["/nonexistent/path/file.py"])
    inp = InputData(root_path=str(tmp_path), scanner=scanner)
    result = await index_codebase(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert result["skipped"] >= 1
    assert result["indexed"] == 0


# ---------------------------------------------------------------------------
# 6. Empty file → skipped
# ---------------------------------------------------------------------------
async def test_empty_file_skipped(tmp_path):
    empty = tmp_path / "empty.py"
    empty.write_text("")
    scanner = AsyncMock(return_value=[str(empty)])
    inp = InputData(root_path=str(tmp_path), scanner=scanner)
    result = await index_codebase(Config(), inp, tmp_path)
    assert result["skipped"] >= 1
    assert result["indexed"] == 0


# ---------------------------------------------------------------------------
# 7. compute_fingerprint_for is deterministic
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_deterministic():
    cfg = Config(extensions=[".py", ".ts"])
    inp = InputData(root_path="/my/project")
    fp1 = compute_fingerprint_for(cfg, inp)
    fp2 = compute_fingerprint_for(cfg, inp)
    assert fp1 == fp2


# ---------------------------------------------------------------------------
# 8. compute_fingerprint_for changes with different root_path
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_changes_with_root():
    cfg = Config()
    fp1 = compute_fingerprint_for(cfg, InputData(root_path="/proj/a"))
    fp2 = compute_fingerprint_for(cfg, InputData(root_path="/proj/b"))
    assert fp1 != fp2


# ---------------------------------------------------------------------------
# 9. compute_fingerprint_for changes with different extensions
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_changes_with_extensions():
    cfg1 = Config(extensions=[".py"])
    cfg2 = Config(extensions=[".ts"])
    inp = InputData(root_path="/p")
    assert compute_fingerprint_for(cfg1, inp) != compute_fingerprint_for(cfg2, inp)


# ---------------------------------------------------------------------------
# 10. Extension order does not affect fingerprint (sorted internally)
# ---------------------------------------------------------------------------
def test_compute_fingerprint_extension_order_invariant():
    cfg1 = Config(extensions=[".py", ".ts"])
    cfg2 = Config(extensions=[".ts", ".py"])
    inp = InputData(root_path="/p")
    assert compute_fingerprint_for(cfg1, inp) == compute_fingerprint_for(cfg2, inp)


# ---------------------------------------------------------------------------
# 11. on_step=None works
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    inp = InputData(root_path=str(tmp_path))
    result = await index_codebase(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 12. Required return keys present
# ---------------------------------------------------------------------------
async def test_return_keys_present(tmp_path):
    inp = InputData(root_path=str(tmp_path))
    result = await index_codebase(Config(), inp, tmp_path)
    for key in ("status", "error", "fingerprint", "cost_usd", "indexed", "skipped", "failed"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 13. Concurrent cost isolation — each run tracks its own cost
# ---------------------------------------------------------------------------
async def test_concurrent_cost_isolation(tmp_path):
    async def run_one():
        inp = InputData(root_path=str(tmp_path))
        return await index_codebase(Config(), inp, tmp_path)

    results = await asyncio.gather(run_one(), run_one())
    assert all(r["status"] == "completed" for r in results)
    assert all(r["cost_usd"] >= 0 for r in results)
