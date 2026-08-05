"""omodul.automata — background automation scheduler (Cron + events).

3O layer: omodul (transaction orchestration).
Core loop: intercept time/events -> assemble a synthetic prompt ->
wake a headless (host-injected) agent to execute silently.

- Cron tasks: register_cron_task("0 9 * * *", "check markets every morning")
- Event triggers: trigger_event("github_push", {...}) -> immediate background run
- Job persistence: JSON on disk, restored on restart (alarms survive reboots)
- Result trail: last N runs kept in memory for the host's notification bar

No Celery: APScheduler(AsyncIO) + native asyncio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

_log = logging.getLogger(__name__)

DEFAULT_JOBS_DB = str(Path.home() / ".veya" / "automata_jobs.json")
_MAX_RESULT_HISTORY = 50

SYNTHETIC_PROMPT_TEMPLATE = """{trigger_context}
System Automation Task Activated.
Task requirement: {task_prompt}

Execute this task using your skills. If you need human approval for a destructive action,
pause and return a HITL (Human-in-the-loop) request. Otherwise, execute and summarize.
"""


class AutomataScheduler:
    """Background daemon: schedule/events -> synthetic prompt -> headless agent."""

    def __init__(
        self,
        execute_callback: Callable[[str], Awaitable[str]],
        *,
        jobs_db_path: str | Path | None = None,
        restore_on_start: bool = True,
    ):
        """
        Args:
            execute_callback: Host-injected runner
                (async (synthetic_prompt: str) -> str) that wakes a headless agent.
            jobs_db_path: Persistence path (default ~/.veya/automata_jobs.json).
            restore_on_start: Restore persisted Cron jobs on startup.
        """
        self.scheduler = AsyncIOScheduler()
        self.execute_callback = execute_callback
        self.jobs_db_path = Path(
            jobs_db_path or os.environ.get("VEYA_AUTOMATA_JOBS_DB", DEFAULT_JOBS_DB)
        ).expanduser()
        self._results: list[dict[str, Any]] = []
        # job_id -> cron expression (persistence; APScheduler 3.x trigger not reversible)
        self._cron_registry: dict[str, str] = {}

        if restore_on_start:
            self._restore_jobs()

        self.scheduler.start()
        _log.info("automata: daemon started, standing by for background tasks.")

    # ── persistence ──────────────────────────────────────────────────
    def _load_jobs_db(self) -> dict:
        if self.jobs_db_path.exists():
            try:
                return json.loads(self.jobs_db_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"jobs": []}

    def _save_jobs_db(self, jobs: list[dict]) -> None:
        self.jobs_db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.jobs_db_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"jobs": jobs}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.jobs_db_path)

    def _persist(self) -> None:
        jobs = []
        for job in self.scheduler.get_jobs():
            cron_expr = self._cron_registry.get(job.id)
            if not cron_expr or not job.id.startswith("cron_"):
                continue  # one-shot event jobs are not persisted
            jobs.append(
                {
                    "id": job.id,
                    "cron_expr": cron_expr,
                    "task_prompt": job.args[1],
                    "created_at": datetime.now().isoformat(),
                }
            )
        self._save_jobs_db(jobs)

    def _restore_jobs(self) -> int:
        """Re-register persisted Cron jobs after a restart. Returns restored count."""
        data = self._load_jobs_db()
        restored = 0
        for entry in data.get("jobs", []):
            try:
                trigger = CronTrigger.from_crontab(entry["cron_expr"])
                self.scheduler.add_job(
                    self._run_headless_mission,
                    trigger=trigger,
                    args=[f"[CRON TRIGGER {entry['cron_expr']}]", entry["task_prompt"]],
                    id=entry["id"],
                    replace_existing=True,
                )
                self._cron_registry[entry["id"]] = entry["cron_expr"]
                restored += 1
            except (ValueError, KeyError) as exc:
                _log.warning("automata: restore job %s failed: %s", entry.get("id"), exc)
        if restored:
            _log.info("automata: restored %d persisted jobs", restored)
        return restored

    # ── task management ──────────────────────────────────────────────
    def register_cron_task(
        self, cron_expr: str, task_prompt: str, task_id: str | None = None
    ) -> str:
        """Register a Cron-scheduled background task (set an alarm)."""
        task_prompt = str(task_prompt).strip()
        if not task_prompt:
            raise ValueError("task_prompt 不能为空")
        if not task_id:
            task_id = f"cron_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}"

        try:
            trigger = CronTrigger.from_crontab(cron_expr)  # e.g. "0 9 * * *"
        except ValueError as exc:
            raise ValueError(f"Cron 表达式无效: {cron_expr} ({exc})") from exc

        self.scheduler.add_job(
            self._run_headless_mission,
            trigger=trigger,
            args=[f"[CRON TRIGGER {cron_expr}]", task_prompt],
            id=task_id,
            replace_existing=True,
        )
        self._cron_registry[task_id] = cron_expr
        self._persist()
        _log.info("automata: cron task registered: %s @ %s", task_id, cron_expr)
        return f"✅ 自动化任务已就绪。ID: {task_id}, 规则: {cron_expr}"

    def remove_task(self, task_id: str) -> str:
        """Cancel a background task (unschedule the alarm)."""
        job = self.scheduler.get_job(task_id)
        if job is None:
            return f"未找到任务: {task_id}"
        self.scheduler.remove_job(task_id)
        self._cron_registry.pop(task_id, None)
        self._persist()
        _log.info("automata: task cancelled: %s", task_id)
        return f"✅ 自动化任务 {task_id} 已取消。"

    def get_jobs(self) -> list[dict]:
        return [
            {
                "id": job.id,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
            for job in self.scheduler.get_jobs()
        ]

    def trigger_event(self, event_name: str, payload: dict) -> str:
        """External system (Webhook, Github) event -> immediate async background run."""
        context = f"[EVENT TRIGGER: {event_name}]"
        task_prompt = f"Context: {json.dumps(payload, ensure_ascii=False)}"
        _log.info("automata: external event received: %s", event_name)
        self.scheduler.add_job(
            self._run_headless_mission,
            args=[context, task_prompt],
            id=f"evt_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        )
        return f"✅ 事件 {event_name} 已受理, 后台代理正在处理。"

    # ── headless execution (core) ────────────────────────────────────
    async def _run_headless_mission(self, trigger_context: str, task_prompt: str) -> str:
        """Core: silently wake the LLM, disguised as a user-initiated task."""
        synthetic_prompt = SYNTHETIC_PROMPT_TEMPLATE.format(
            trigger_context=trigger_context, task_prompt=task_prompt
        )
        _log.info("automata headless run: %s", trigger_context[:60])
        try:
            result = await self.execute_callback(synthetic_prompt)
            status = "success"
        except asyncio.CancelledError:
            self._results.append(
                {
                    "trigger": trigger_context,
                    "timestamp": datetime.now().isoformat(),
                    "status": "cancelled",
                    "result": "task cancelled by shutdown",
                }
            )
            raise
        except Exception as exc:  # noqa: BLE001 — a failed background task must not kill the daemon
            result = f"HEADLESS FAILED: {type(exc).__name__}: {exc}"
            status = "failed"
            _log.error("automata: background task crashed: %s", exc)
        self._results.append(
            {
                "trigger": trigger_context,
                "timestamp": datetime.now().isoformat(),
                "status": status,
                "result": str(result)[:2000],
            }
        )
        self._results = self._results[-_MAX_RESULT_HISTORY:]
        _log.info("automata result (%s): %s", status, str(result)[:120])
        return str(result)

    # ── lifecycle ────────────────────────────────────────────────────
    def get_recent_results(self, limit: int = 10) -> list[dict]:
        return self._results[-limit:]

    def get_status(self) -> dict:
        return {
            "running": self.scheduler.running,
            "jobs": self.get_jobs(),
            "recent_results": len(self._results),
        }

    def shutdown(self) -> None:
        """Graceful stop: jobs persisted, restored on next start."""
        try:
            self._persist()
            self.scheduler.shutdown(wait=False)
            _log.info("automata: daemon stopped.")
        except Exception:  # noqa: BLE001 — shutdown must not raise on scheduler state
            pass
