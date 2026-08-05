"""omodul.task_manager — transactional task state machine with resume/rollback.

3O layer: omodul (transaction orchestration).
Composes oprim._task_state (pure state transitions) with obase.task_store
(SQLite durable backing). Every checkpoint is a committed transaction:
a crash/restart can resume from the last persisted step, and Time-Travel
rollback replays from an earlier step.
"""

from __future__ import annotations

import logging
from typing import Any

from obase.task_store import TaskStore
from oprim._task_state import (
    FAILED,
    PAUSED,
    RUNNING,
    SUCCESS,
    advance_step,
    build_steps,
    rollback_to,
    summary,
)

_log = logging.getLogger(__name__)


class TaskManager:
    """Step-based task lifecycle: create -> checkpoint -> resume / rollback."""

    def __init__(self, store: TaskStore | None = None):
        self.store = store or TaskStore()

    # ── 生命周期 ─────────────────────────────────────────────────────
    def create_task(
        self, task_id: str, total_steps: int, initial_payload: dict | None = None
    ) -> str:
        """Create a new task DAG with the given step count."""
        steps = build_steps(total_steps, initial_payload)
        self.store.create(task_id, total_steps, steps)
        _log.info("task_manager: task %s created (%d steps)", task_id, total_steps)
        return task_id

    def checkpoint(
        self,
        task_id: str,
        step_index: int,
        step_payload: dict | None = None,
        status: str = RUNNING,
    ) -> dict[str, Any]:
        """Persist one step's completion as the new resume point.

        Returns the updated resume context (ready for the next step).
        """
        ctx = self.get_resume_context(task_id)
        steps, next_index, done = advance_step(
            ctx["steps"], step_index, step_payload or {}, ctx["total_steps"]
        )
        final_status = SUCCESS if done else (status if status in (RUNNING, PAUSED) else RUNNING)
        persist_index = step_index if done else next_index
        self.store.checkpoint(
            task_id, status=final_status, current_step=persist_index, steps=steps
        )
        return {
            "status": final_status,
            "current_step": persist_index,
            "steps": steps,
            "done": done,
        }

    def get_resume_context(self, task_id: str) -> dict[str, Any]:
        """Resume context from the crash point: {status, current_step, total_steps, steps}."""
        snapshot = self.store.load(task_id)
        if snapshot is None:
            raise ValueError(f"Task {task_id} not found.")
        return {
            "status": snapshot["status"],
            "current_step": snapshot["current_step"],
            "total_steps": snapshot["total_steps"],
            "steps": snapshot["steps"],
        }

    # ── 终态/控制 ────────────────────────────────────────────────────
    def complete(self, task_id: str) -> None:
        self.store.mark_status(task_id, SUCCESS)

    def fail(self, task_id: str) -> None:
        self.store.mark_status(task_id, FAILED)

    def pause(self, task_id: str) -> None:
        self.store.mark_status(task_id, PAUSED)

    def rollback(self, task_id: str, to_step: int) -> dict[str, Any]:
        """Time-Travel: rewind to an earlier step (later steps reset to PENDING).

        Returns the new resume context replayed from ``to_step``.
        """
        ctx = self.get_resume_context(task_id)
        rolled = rollback_to(ctx["steps"], to_step)
        self.store.checkpoint(
            task_id, status=RUNNING, current_step=to_step, steps=rolled
        )
        _log.info("task_manager: task %s rolled back to step %d", task_id, to_step)
        return {
            "status": RUNNING,
            "current_step": to_step,
            "total_steps": ctx["total_steps"],
            "steps": rolled,
        }

    # ── 查询 ─────────────────────────────────────────────────────────
    def list_tasks(self) -> list[dict[str, Any]]:
        return self.store.list_tasks()

    def task_summary(self, task_id: str) -> dict[str, Any]:
        ctx = self.get_resume_context(task_id)
        return {**summary(ctx["steps"]), "status": ctx["status"]}
