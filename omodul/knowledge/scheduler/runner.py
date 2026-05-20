"""ScheduledJobRunner — invokes an agent for a scheduled job and records the run."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime

from oprim._logging import log

from omodul.knowledge.agents.registry import get_registry
from omodul.knowledge.agents.runner import AgentRunner
from omodul.knowledge.agents.tracer import AgentTracer

from .job_store import JobStore
from .notifier import Notifier


class ScheduledJobRunner:
    """Resolves agent from registry and runs it for a scheduled job."""

    def __init__(
        self,
        job_store: JobStore,
        notifier: Notifier,
    ) -> None:
        self.job_store = job_store
        self.notifier = notifier
        self._agent_runner = AgentRunner(AgentTracer())

    async def run(self, job_id: str) -> None:
        try:
            job = self.job_store.get(job_id)
        except Exception as exc:
            log.error("scheduled_job_not_found", job_id=job_id, error=str(exc))
            return

        registry = get_registry()
        try:
            agent_cls = registry.get(job["agent_name"])
        except Exception as exc:
            log.error(
                "scheduled_job_agent_not_found",
                job_id=job_id,
                agent=job["agent_name"],
                error=str(exc),
            )
            return

        agent = agent_cls()
        params = json.loads(job.get("agent_params") or "{}")
        run_id = str(uuid.uuid4())
        started_at = datetime.utcnow()

        self.job_store.create_run(
            run_id=run_id,
            job_id=job_id,
            status="running",
            started_at=started_at,
        )

        log.info(
            "scheduled_job_run_started",
            job=job["name"],
            agent=job["agent_name"],
            run_id=run_id,
        )

        try:
            result = await asyncio.wait_for(
                self._agent_runner.run(
                    agent=agent,
                    user_id=job["user_id"],
                    params=params,
                ),
                timeout=job.get("max_runtime_seconds", 1800),
            )
            self.job_store.update_run(
                run_id=run_id,
                status="completed",
                agent_run_id=result.output.get("_agent_run_id"),
                completed_at=datetime.utcnow(),
            )
            log.info("scheduled_job_run_completed", job=job["name"], run_id=run_id)
            if job.get("notify_on_completion", True):
                await self.notifier.notify_completion(job, result.output)

        except asyncio.TimeoutError:
            self.job_store.update_run(
                run_id=run_id,
                status="timeout",
                error_message=f"exceeded max_runtime_seconds={job.get('max_runtime_seconds', 1800)}",
                completed_at=datetime.utcnow(),
            )
            log.error("scheduled_job_run_timeout", job=job["name"], run_id=run_id)
            if job.get("notify_on_failure", True):
                await self.notifier.notify_failure(job, "Job timed out")

        except Exception as exc:
            self.job_store.update_run(
                run_id=run_id,
                status="failed",
                error_message=str(exc),
                completed_at=datetime.utcnow(),
            )
            log.error("scheduled_job_run_failed", job=job["name"], run_id=run_id, error=str(exc))
            if job.get("notify_on_failure", True):
                await self.notifier.notify_failure(job, str(exc))
