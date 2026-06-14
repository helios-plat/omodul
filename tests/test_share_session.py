"""Tests for omodul.share_session."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from omodul.share_session import Config, InputData, share_session, compute_fingerprint_for


# ---------------------------------------------------------------------------
# 1. Happy path with session_data → share_url returned
# ---------------------------------------------------------------------------
async def test_happy_path_returns_completed(tmp_path):
    inp = InputData(session_id="s1", session_data={"title": "My session"})
    result = await share_session(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    assert "share_url" in result


# ---------------------------------------------------------------------------
# 2. Default URL contains session_id
# ---------------------------------------------------------------------------
async def test_default_url_contains_session_id(tmp_path):
    inp = InputData(session_id="my-session-id", session_data={})
    result = await share_session(Config(), inp, tmp_path)
    assert "my-session-id" in result["share_url"]


# ---------------------------------------------------------------------------
# 3. CancelledError re-raise
# ---------------------------------------------------------------------------
async def test_cancelled_error_propagates(tmp_path):
    async def slow_uploader(**kw):
        await asyncio.sleep(100)

    inp = InputData(session_id="s2", session_data={}, uploader=slow_uploader)
    task = asyncio.create_task(share_session(Config(), inp, tmp_path))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 4. Exception → status="failed"
# ---------------------------------------------------------------------------
async def test_exception_returns_failed(tmp_path):
    def bad_loader(**kw):
        raise RuntimeError("load error")

    inp = InputData(session_id="s3", loader=bad_loader)
    result = await share_session(Config(), inp, tmp_path)
    assert result["status"] == "failed"
    assert result["error"]["type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# 5. Sensitive keys are redacted
# ---------------------------------------------------------------------------
async def test_sensitive_keys_redacted(tmp_path):
    data = {"title": "test", "api_key": "sk-secret", "token": "tok123", "normal": "keep"}
    inp = InputData(session_id="s4", session_data=data)
    result = await share_session(Config(), inp, tmp_path)
    assert result["status"] == "completed"
    # The uploader receives redacted payload — verify via uploader capture
    captured = {}

    async def capture_uploader(payload):
        captured.update(payload)
        return "https://share.example.com/s4"

    inp2 = InputData(session_id="s4", session_data=data, uploader=capture_uploader)
    await share_session(Config(), inp2, tmp_path)
    assert captured["api_key"] == "***REDACTED***"
    assert captured["token"] == "***REDACTED***"
    assert captured["normal"] == "keep"


# ---------------------------------------------------------------------------
# 6. Loader is called when no session_data
# ---------------------------------------------------------------------------
async def test_loader_called_when_no_session_data(tmp_path):
    loader = AsyncMock(return_value={"title": "loaded"})
    inp = InputData(session_id="s5", loader=loader)
    await share_session(Config(), inp, tmp_path)
    loader.assert_called_once_with(session_id="s5")


# ---------------------------------------------------------------------------
# 7. Uploader receives redacted payload and its URL is returned
# ---------------------------------------------------------------------------
async def test_uploader_url_returned(tmp_path):
    uploader = AsyncMock(return_value="https://custom.share/xyz")
    inp = InputData(session_id="s6", session_data={"x": 1}, uploader=uploader)
    result = await share_session(Config(), inp, tmp_path)
    assert result["share_url"] == "https://custom.share/xyz"


# ---------------------------------------------------------------------------
# 8. compute_fingerprint_for is deterministic
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_deterministic():
    cfg = Config()
    inp = InputData(session_id="sid-xyz")
    fp1 = compute_fingerprint_for(cfg, inp)
    fp2 = compute_fingerprint_for(cfg, inp)
    assert fp1 == fp2


# ---------------------------------------------------------------------------
# 9. compute_fingerprint_for changes with different session_id
# ---------------------------------------------------------------------------
def test_compute_fingerprint_for_changes_with_session_id():
    cfg = Config()
    fp1 = compute_fingerprint_for(cfg, InputData(session_id="A"))
    fp2 = compute_fingerprint_for(cfg, InputData(session_id="B"))
    assert fp1 != fp2


# ---------------------------------------------------------------------------
# 10. on_step=None works
# ---------------------------------------------------------------------------
async def test_on_step_none_no_error(tmp_path):
    inp = InputData(session_id="s7", session_data={})
    result = await share_session(Config(), inp, tmp_path, on_step=None)
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# 11. Required return keys present
# ---------------------------------------------------------------------------
async def test_return_keys_present(tmp_path):
    inp = InputData(session_id="s8", session_data={})
    result = await share_session(Config(), inp, tmp_path)
    for key in ("status", "error", "fingerprint", "share_url"):
        assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# 12. Concurrent calls produce independent results
# ---------------------------------------------------------------------------
async def test_concurrent_calls_independent(tmp_path):
    async def run_one(sid):
        inp = InputData(session_id=sid, session_data={"k": sid})
        return await share_session(Config(), inp, tmp_path)

    results = await asyncio.gather(run_one("sid-c1"), run_one("sid-c2"))
    assert all(r["status"] == "completed" for r in results)
    urls = [r["share_url"] for r in results]
    assert urls[0] != urls[1]


# ---------------------------------------------------------------------------
# 13. Empty session_data — no error, empty dict treated as valid
# ---------------------------------------------------------------------------
async def test_empty_session_data_no_error(tmp_path):
    inp = InputData(session_id="s9", session_data={})
    result = await share_session(Config(), inp, tmp_path)
    assert result["status"] == "completed"
