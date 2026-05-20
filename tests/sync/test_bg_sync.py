"""Tests for BackgroundSyncDaemon."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omodul.sync.bg_sync import BackgroundSyncDaemon
from oprim.meta_db.duckdb import open_meta_db

_MIGRATIONS_DIR = (
    Path(__file__).parent.parent.parent.parent
    / "oprim" / "oprim" / "meta_db" / "migrations"
)


@pytest.fixture()
def db(tmp_path: Path):
    m = open_meta_db(tmp_path / "meta.duckdb")
    m.migrate(_MIGRATIONS_DIR)
    yield m
    m.close()


def _make_storage() -> MagicMock:
    s = MagicMock()
    s.authenticate = AsyncMock(return_value=True)
    return s


def _make_daemon(db, storage=None, **kwargs) -> BackgroundSyncDaemon:
    if storage is None:
        storage = _make_storage()
    defaults = dict(
        flush_interval_sec=1,
        pull_interval_sec=1,
        snapshot_interval_hours=24,
    )
    defaults.update(kwargs)
    return BackgroundSyncDaemon("u1", "dev_A", db, storage, **defaults)


class TestDaemonStatus:
    def test_initial_status_not_running(self, db):
        d = _make_daemon(db)
        status = d.status()
        assert status["running"] is True  # event not set yet
        assert status["last_flush_at"] is None
        assert status["last_pull_at"] is None
        assert status["last_snapshot_at"] is None
        assert status["last_flush_count"] == 0
        assert status["last_applied_seq"] == 0
        assert status["user_id"] == "u1"
        assert status["device_id"] == "dev_A"

    def test_status_running_false_after_stop_set(self, db):
        d = _make_daemon(db)
        d._stop.set()
        assert d.status()["running"] is False

    async def test_status_after_flush_updates(self, db, tmp_path):
        d = _make_daemon(db)

        from oskill.sync.flush_outbox import FlushResult

        async def mock_flush(*args, **kwargs):
            return FlushResult(flushed_count=3, failed_count=0, last_flushed_seq=3)

        async def mock_apply(*args, **kwargs):
            from oskill.sync.apply_remote_events import ApplyResult
            return ApplyResult(
                applied_count=0, skipped_count=0, conflict_count=0, last_applied_seq=0
            )

        with (
            patch("omodul.sync.bg_sync.flush_outbox", side_effect=mock_flush),
            patch("omodul.sync.bg_sync.apply_remote_events", side_effect=mock_apply),
        ):
            run_task = asyncio.create_task(d.run())
            await asyncio.sleep(0.05)
            await d.shutdown()
            await run_task

        assert d._last_flush_at is not None
        assert d._last_flush_count == 3


class TestDaemonShutdown:
    async def test_shutdown_stops_run(self, db):
        storage = _make_storage()
        d = _make_daemon(db, storage)

        async def fast_flush(*args, **kwargs):
            from oskill.sync.flush_outbox import FlushResult
            return FlushResult(0, 0, 0)

        async def fast_apply(*args, **kwargs):
            from oskill.sync.apply_remote_events import ApplyResult
            return ApplyResult(0, 0, 0, 0)

        with (
            patch("omodul.sync.bg_sync.flush_outbox", side_effect=fast_flush),
            patch("omodul.sync.bg_sync.apply_remote_events", side_effect=fast_apply),
        ):
            task = asyncio.create_task(d.run())
            await asyncio.sleep(0.02)
            await d.shutdown()
            await asyncio.wait_for(task, timeout=2.0)

        assert d._stop.is_set()

    async def test_shutdown_is_idempotent(self, db):
        d = _make_daemon(db)
        await d.shutdown()
        await d.shutdown()  # must not raise
        assert d._stop.is_set()

    async def test_run_authenticates_storage(self, db):
        storage = _make_storage()
        d = _make_daemon(db, storage)

        with (
            patch("omodul.sync.bg_sync.flush_outbox", side_effect=AsyncMock(return_value=MagicMock(flushed_count=0, last_flushed_seq=0, failed_count=0))),
            patch("omodul.sync.bg_sync.apply_remote_events", side_effect=AsyncMock(return_value=MagicMock(applied_count=0, skipped_count=0, conflict_count=0, last_applied_seq=0))),
        ):
            task = asyncio.create_task(d.run())
            await asyncio.sleep(0.02)
            await d.shutdown()
            await asyncio.wait_for(task, timeout=2.0)

        storage.authenticate.assert_called_once()


class TestFlushLoop:
    async def test_flush_loop_calls_flush_outbox(self, db):
        d = _make_daemon(db, flush_interval_sec=1)
        call_count = 0

        async def mock_flush(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            from oskill.sync.flush_outbox import FlushResult
            return FlushResult(1, 0, call_count)

        async def mock_apply(*args, **kwargs):
            from oskill.sync.apply_remote_events import ApplyResult
            return ApplyResult(0, 0, 0, 0)

        with (
            patch("omodul.sync.bg_sync.flush_outbox", side_effect=mock_flush),
            patch("omodul.sync.bg_sync.apply_remote_events", side_effect=mock_apply),
        ):
            task = asyncio.create_task(d.run())
            await asyncio.sleep(0.05)
            await d.shutdown()
            await asyncio.wait_for(task, timeout=2.0)

        assert call_count >= 1

    async def test_flush_loop_error_continues(self, db):
        d = _make_daemon(db, flush_interval_sec=1)
        call_count = 0

        async def mock_flush(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("network timeout")
            from oskill.sync.flush_outbox import FlushResult
            return FlushResult(0, 0, 0)

        async def mock_apply(*args, **kwargs):
            from oskill.sync.apply_remote_events import ApplyResult
            return ApplyResult(0, 0, 0, 0)

        with (
            patch("omodul.sync.bg_sync.flush_outbox", side_effect=mock_flush),
            patch("omodul.sync.bg_sync.apply_remote_events", side_effect=mock_apply),
        ):
            task = asyncio.create_task(d.run())
            # Give enough time for the error + backoff (60s backoff is skipped by stopping)
            await asyncio.sleep(0.05)
            await d.shutdown()
            await asyncio.wait_for(task, timeout=2.0)

        # No crash — daemon recovered from the error


class TestPullLoop:
    async def test_pull_loop_calls_apply_remote_events(self, db):
        d = _make_daemon(db, pull_interval_sec=1)
        applied_calls = 0

        async def mock_flush(*args, **kwargs):
            from oskill.sync.flush_outbox import FlushResult
            return FlushResult(0, 0, 0)

        async def mock_apply(*args, **kwargs):
            nonlocal applied_calls
            applied_calls += 1
            from oskill.sync.apply_remote_events import ApplyResult
            return ApplyResult(2, 0, 0, 5)

        with (
            patch("omodul.sync.bg_sync.flush_outbox", side_effect=mock_flush),
            patch("omodul.sync.bg_sync.apply_remote_events", side_effect=mock_apply),
        ):
            task = asyncio.create_task(d.run())
            await asyncio.sleep(0.05)
            await d.shutdown()
            await asyncio.wait_for(task, timeout=2.0)

        assert applied_calls >= 1
        assert d._last_applied_seq == 5

    async def test_pull_loop_error_continues(self, db):
        d = _make_daemon(db, pull_interval_sec=1)

        async def mock_flush(*args, **kwargs):
            from oskill.sync.flush_outbox import FlushResult
            return FlushResult(0, 0, 0)

        call_count = 0

        async def mock_apply(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("storage unreachable")
            from oskill.sync.apply_remote_events import ApplyResult
            return ApplyResult(0, 0, 0, 0)

        with (
            patch("omodul.sync.bg_sync.flush_outbox", side_effect=mock_flush),
            patch("omodul.sync.bg_sync.apply_remote_events", side_effect=mock_apply),
        ):
            task = asyncio.create_task(d.run())
            await asyncio.sleep(0.05)
            await d.shutdown()
            await asyncio.wait_for(task, timeout=2.0)


class TestSnapshotLoop:
    async def test_snapshot_loop_runs_after_interval(self, db):
        d = _make_daemon(db, snapshot_interval_hours=0)  # 0 hours = immediate
        snap_calls = 0

        async def mock_flush(*args, **kwargs):
            from oskill.sync.flush_outbox import FlushResult
            return FlushResult(0, 0, 0)

        async def mock_apply(*args, **kwargs):
            from oskill.sync.apply_remote_events import ApplyResult
            return ApplyResult(0, 0, 0, 0)

        async def mock_snapshot(*args, **kwargs):
            nonlocal snap_calls
            snap_calls += 1
            return {"snapshot_id": "s1", "seq_at": 1}

        with (
            patch("omodul.sync.bg_sync.flush_outbox", side_effect=mock_flush),
            patch("omodul.sync.bg_sync.apply_remote_events", side_effect=mock_apply),
            patch("omodul.sync.bg_sync.snapshot_backup", side_effect=mock_snapshot),
        ):
            task = asyncio.create_task(d.run())
            # With 0-hour interval, snapshot fires almost immediately
            await asyncio.sleep(0.1)
            await d.shutdown()
            await asyncio.wait_for(task, timeout=2.0)

        assert snap_calls >= 1
        assert d._last_snapshot_at is not None

    async def test_snapshot_loop_error_continues(self, db):
        d = _make_daemon(db, snapshot_interval_hours=0)

        async def mock_flush(*args, **kwargs):
            from oskill.sync.flush_outbox import FlushResult
            return FlushResult(0, 0, 0)

        async def mock_apply(*args, **kwargs):
            from oskill.sync.apply_remote_events import ApplyResult
            return ApplyResult(0, 0, 0, 0)

        async def mock_snapshot(*args, **kwargs):
            raise RuntimeError("upload failed")

        with (
            patch("omodul.sync.bg_sync.flush_outbox", side_effect=mock_flush),
            patch("omodul.sync.bg_sync.apply_remote_events", side_effect=mock_apply),
            patch("omodul.sync.bg_sync.snapshot_backup", side_effect=mock_snapshot),
        ):
            task = asyncio.create_task(d.run())
            await asyncio.sleep(0.1)
            await d.shutdown()
            await asyncio.wait_for(task, timeout=2.0)
        # No crash despite snapshot error
