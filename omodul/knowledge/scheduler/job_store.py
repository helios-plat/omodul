"""DuckDB-backed scheduled job persistence."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from oprim.meta_db import MetaDB, open_meta_db
from oskill.knowledge._context import meta_db_path

from .errors import JobNotFoundError


class JobStore:
    """CRUD operations for scheduled_jobs and scheduled_job_runs tables."""

    def __init__(self, db: MetaDB | None = None) -> None:
        self._db = db

    def _get_db(self) -> MetaDB:
        if self._db is not None:
            return self._db
        return open_meta_db(meta_db_path())

    def create(self, spec: dict) -> dict:
        db = self._get_db()
        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        db.execute(
            """
            INSERT INTO scheduled_jobs (
                id, user_id, name, agent_name, agent_params,
                cron_expression, timezone, enabled,
                notify_on_completion, notify_on_failure,
                max_runtime_seconds, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                job_id,
                spec["user_id"],
                spec["name"],
                spec["agent_name"],
                json.dumps(spec.get("agent_params", {})),
                spec["cron_expression"],
                spec.get("timezone", "Asia/Shanghai"),
                spec.get("enabled", True),
                spec.get("notify_on_completion", True),
                spec.get("notify_on_failure", True),
                spec.get("max_runtime_seconds", 1800),
                now,
                now,
            ],
        )
        return self.get(job_id)

    def get(self, job_id: str) -> dict:
        db = self._get_db()
        rows = db.fetchall(
            "SELECT * FROM scheduled_jobs WHERE id = ?", [job_id]
        )
        if not rows:
            raise JobNotFoundError(f"Scheduled job not found: {job_id!r}")
        return self._row_to_dict(rows[0])

    def find_by_name(self, user_id: str, name: str) -> dict | None:
        db = self._get_db()
        rows = db.fetchall(
            "SELECT * FROM scheduled_jobs WHERE user_id = ? AND name = ?",
            [user_id, name],
        )
        return self._row_to_dict(rows[0]) if rows else None

    def list_enabled_jobs(self) -> list[dict]:
        db = self._get_db()
        rows = db.fetchall(
            "SELECT * FROM scheduled_jobs WHERE enabled = TRUE"
        )
        return [self._row_to_dict(r) for r in rows]

    def list_jobs(self, user_id: str) -> list[dict]:
        db = self._get_db()
        rows = db.fetchall(
            "SELECT * FROM scheduled_jobs WHERE user_id = ? ORDER BY created_at DESC",
            [user_id],
        )
        return [self._row_to_dict(r) for r in rows]

    def update(self, job_id: str, updates: dict) -> dict:
        db = self._get_db()
        updates["updated_at"] = datetime.utcnow().isoformat()
        allowed = {
            "enabled", "cron_expression", "timezone", "agent_params",
            "notify_on_completion", "notify_on_failure", "max_runtime_seconds",
            "updated_at",
        }
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            return self.get(job_id)
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE scheduled_jobs SET {sets} WHERE id = ?",
            [*fields.values(), job_id],
        )
        return self.get(job_id)

    def delete(self, job_id: str) -> None:
        db = self._get_db()
        db.execute("DELETE FROM scheduled_jobs WHERE id = ?", [job_id])
        db.execute("DELETE FROM scheduled_job_runs WHERE job_id = ?", [job_id])

    # --- Run history ---

    def create_run(
        self, run_id: str, job_id: str, status: str, started_at: datetime
    ) -> None:
        db = self._get_db()
        db.execute(
            """
            INSERT INTO scheduled_job_runs (id, job_id, status, started_at)
            VALUES (?, ?, ?, ?)
            """,
            [run_id, job_id, status, started_at.isoformat()],
        )

    def update_run(
        self,
        run_id: str,
        status: str,
        agent_run_id: str | None = None,
        error_message: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        db = self._get_db()
        db.execute(
            """
            UPDATE scheduled_job_runs
            SET status = ?, agent_run_id = ?, error_message = ?, completed_at = ?
            WHERE id = ?
            """,
            [
                status,
                agent_run_id,
                error_message,
                (completed_at or datetime.utcnow()).isoformat(),
                run_id,
            ],
        )

    def list_runs(self, job_id: str, limit: int = 50) -> list[dict]:
        db = self._get_db()
        rows = db.fetchall(
            "SELECT * FROM scheduled_job_runs WHERE job_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            [job_id, limit],
        )
        cols = ["id", "job_id", "agent_run_id", "status", "started_at", "completed_at", "error_message"]
        return [dict(zip(cols, r)) for r in rows]

    @staticmethod
    def _row_to_dict(row: tuple) -> dict:
        cols = [
            "id", "user_id", "name", "agent_name", "agent_params",
            "cron_expression", "timezone", "enabled",
            "notify_on_completion", "notify_on_failure",
            "max_runtime_seconds", "created_at", "updated_at",
        ]
        return dict(zip(cols, row))
