"""APScheduler-based cron engine for Stratum scheduled jobs."""
from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from oprim._logging import log

from .job_store import JobStore
from .notifier import Notifier
from .run_lock import RunLock
from .runner import ScheduledJobRunner


class CronEngine:
    """Wraps APScheduler 3.x with job persistence + Redis locking."""

    def __init__(
        self,
        job_store: JobStore,
        run_lock: RunLock,
        runner: ScheduledJobRunner,
    ) -> None:
        self.job_store = job_store
        self.run_lock = run_lock
        self.runner = runner
        self._scheduler = AsyncIOScheduler()

    async def start(self) -> None:
        jobs = self.job_store.list_enabled_jobs()
        for job in jobs:
            self._schedule(job)
        self._scheduler.start()
        log.info("scheduler_started", jobs=len(jobs))

    async def stop(self) -> None:
        self._scheduler.shutdown(wait=True)
        log.info("scheduler_stopped")

    def _schedule(self, job: dict) -> None:
        trigger = CronTrigger.from_crontab(
            job["cron_expression"],
            timezone=job.get("timezone", "Asia/Shanghai"),
        )
        self._scheduler.add_job(
            self._wrapped_run,
            trigger=trigger,
            args=[job["id"]],
            id=job["id"],
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=300,
        )

    async def _wrapped_run(self, job_id: str) -> None:
        async with self.run_lock.acquire(job_id) as lock:
            if not lock.acquired:
                log.info("scheduler_skip_locked", job_id=job_id)
                return
            await self.runner.run(job_id)

    async def create_job(self, spec: dict) -> dict:
        job = self.job_store.create(spec)
        if job.get("enabled", True):
            self._schedule(job)
        return job

    async def delete_job(self, job_id: str) -> None:
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        self.job_store.delete(job_id)

    async def enable_job(self, job_id: str) -> dict:
        job = self.job_store.update(job_id, {"enabled": True})
        self._schedule(job)
        return job

    async def disable_job(self, job_id: str) -> dict:
        job = self.job_store.update(job_id, {"enabled": False})
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        return job

    async def run_now(self, job_id: str) -> None:
        """Immediately execute a job, bypassing cron schedule."""
        async with self.run_lock.acquire(job_id) as lock:
            if not lock.acquired:
                log.warning("scheduler_run_now_locked", job_id=job_id)
            await self.runner.run(job_id)
