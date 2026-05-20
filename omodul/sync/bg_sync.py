"""omodul.sync.bg_sync — BackgroundSyncDaemon for Stratum multi-device sync."""
from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone
from typing import Any

from oprim._logging import log
from oprim.meta_db.duckdb import MetaDB
from oskill.sync import (
    apply_remote_events,
    flush_outbox,
    snapshot_backup,
)


class BackgroundSyncDaemon:
    """Runs periodic flush, pull, and snapshot loops in the background.

    Usage::

        daemon = BackgroundSyncDaemon(user_id, device_id, db, storage)
        await daemon.run()   # blocks until shutdown()

    Or start as a task::

        task = asyncio.create_task(daemon.run())
        ...
        await daemon.shutdown()
        await task
    """

    def __init__(
        self,
        user_id: str,
        device_id: str,
        db: MetaDB,
        storage: Any,
        *,
        flush_interval_sec: int = 30,
        pull_interval_sec: int = 60,
        snapshot_interval_hours: int = 24,
    ) -> None:
        self.user_id = user_id
        self.device_id = device_id
        self.db = db
        self.storage = storage
        self.flush_interval = flush_interval_sec
        self.pull_interval = pull_interval_sec
        self.snapshot_interval = snapshot_interval_hours * 3600

        self._stop = asyncio.Event()
        self._last_flush_at: datetime | None = None
        self._last_pull_at: datetime | None = None
        self._last_snapshot_at: datetime | None = None
        self._last_flush_count: int = 0
        self._last_applied_seq: int = 0

    async def run(self) -> None:
        """Authenticate storage, start all loops, block until shutdown()."""
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(
            signal.SIGTERM,
            lambda: asyncio.ensure_future(self.shutdown()),
        )

        await self.storage.authenticate()
        log.info(
            "bg_sync_started",
            user_id=self.user_id,
            device_id=self.device_id,
            flush_interval=self.flush_interval,
            pull_interval=self.pull_interval,
        )

        tasks = [
            asyncio.create_task(self._flush_loop(), name="flush_loop"),
            asyncio.create_task(self._pull_loop(), name="pull_loop"),
            asyncio.create_task(self._snapshot_loop(), name="snapshot_loop"),
        ]

        await self._stop.wait()

        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        log.info("bg_sync_stopped", user_id=self.user_id)

    async def _flush_loop(self) -> None:
        """Periodically flush local changefeed events to remote storage."""
        while not self._stop.is_set():
            try:
                result = await flush_outbox(
                    self.user_id,
                    self.device_id,
                    self.db,
                    self.storage,
                )
                self._last_flush_at = datetime.now(tz=timezone.utc)
                self._last_flush_count = result.flushed_count
                log.info(
                    "flush_loop_ok",
                    flushed=result.flushed_count,
                    seq=result.last_flushed_seq,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("flush_loop_error", error=str(exc))
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._stop.wait()), timeout=60.0
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop.wait()),
                    timeout=float(self.flush_interval),
                )
            except asyncio.TimeoutError:
                pass

    async def _pull_loop(self) -> None:
        """Periodically download and apply remote changefeed events."""
        while not self._stop.is_set():
            try:
                result = await apply_remote_events(
                    self.user_id,
                    self.device_id,
                    self.db,
                    self.storage,
                )
                self._last_pull_at = datetime.now(tz=timezone.utc)
                self._last_applied_seq = result.last_applied_seq
                log.info(
                    "pull_loop_ok",
                    applied=result.applied_count,
                    conflicts=result.conflict_count,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("pull_loop_error", error=str(exc))
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._stop.wait()), timeout=60.0
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop.wait()),
                    timeout=float(self.pull_interval),
                )
            except asyncio.TimeoutError:
                pass

    async def _snapshot_loop(self) -> None:
        """Periodically take a full-state snapshot and upload to remote storage."""
        while not self._stop.is_set():
            # Wait for snapshot_interval before first snapshot
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop.wait()),
                    timeout=float(self.snapshot_interval),
                )
            except asyncio.TimeoutError:
                pass

            if self._stop.is_set():
                break

            try:
                result = await snapshot_backup(
                    self.user_id,
                    self.device_id,
                    self.db,
                    self.storage,
                )
                self._last_snapshot_at = datetime.now(tz=timezone.utc)
                log.info(
                    "snapshot_loop_ok",
                    snapshot_id=result.get("snapshot_id"),
                    seq_at=result.get("seq_at"),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("snapshot_loop_error", error=str(exc))

    async def shutdown(self) -> None:
        """Signal all loops to stop; run() will return after cleanup."""
        log.info("bg_sync_shutdown_requested", user_id=self.user_id)
        self._stop.set()

    def status(self) -> dict:
        """Return a snapshot of daemon activity."""
        return {
            "user_id": self.user_id,
            "device_id": self.device_id,
            "running": not self._stop.is_set(),
            "last_flush_at": self._last_flush_at.isoformat() if self._last_flush_at else None,
            "last_pull_at": self._last_pull_at.isoformat() if self._last_pull_at else None,
            "last_snapshot_at": self._last_snapshot_at.isoformat() if self._last_snapshot_at else None,
            "last_flush_count": self._last_flush_count,
            "last_applied_seq": self._last_applied_seq,
        }
