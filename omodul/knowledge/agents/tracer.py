"""Agent run trace — persisted to DuckDB via oprim.meta_db."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from oprim.meta_db import MetaDB, open_meta_db
from oskill.knowledge._context import meta_db_path

from .base import AgentResult, AgentStep, Citation


def _step_to_dict(step: AgentStep) -> dict:
    return {
        "step_num": step.step_num,
        "tool_name": step.tool_name,
        "tool_input": step.tool_input,
        "tool_output": step.tool_output,
        "duration_ms": step.duration_ms,
        "error": step.error,
        "timestamp": step.timestamp.isoformat(),
    }


def _citation_to_dict(c: Citation) -> dict:
    return {
        "substrate_id": c.substrate_id,
        "fragment_id": c.fragment_id,
        "anchor": c.anchor,
        "deep_link": c.deep_link,
    }


class AgentTracer:
    """Persists agent run trace to DuckDB."""

    def __init__(self, db: MetaDB | None = None) -> None:
        self._db = db

    def _get_db(self) -> MetaDB:
        if self._db is not None:
            return self._db
        return open_meta_db(meta_db_path())

    def create_run(
        self,
        run_id: str,
        user_id: str,
        agent_name: str,
        params: dict,
        started_at: datetime,
    ) -> None:
        db = self._get_db()
        db.execute(
            """
            INSERT INTO agent_runs
                (id, user_id, agent_name, params, status, started_at)
            VALUES (?, ?, ?, ?, 'running', ?)
            """,
            [run_id, user_id, agent_name, json.dumps(params), started_at.isoformat()],
        )

    def complete_run(
        self,
        run_id: str,
        status: str,
        trace: list[AgentStep] | None = None,
        citations: list[Citation] | None = None,
        output: dict | None = None,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        cost_usd: float = 0.0,
        error_message: str | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        db = self._get_db()
        db.execute(
            """
            UPDATE agent_runs
            SET status = ?,
                trace = ?,
                citations = ?,
                output = ?,
                total_input_tokens = ?,
                total_output_tokens = ?,
                cost_usd = ?,
                error_message = ?,
                completed_at = ?
            WHERE id = ?
            """,
            [
                status,
                json.dumps([_step_to_dict(s) for s in (trace or [])]),
                json.dumps([_citation_to_dict(c) for c in (citations or [])]),
                json.dumps(output) if output is not None else None,
                total_input_tokens,
                total_output_tokens,
                cost_usd,
                error_message,
                (completed_at or datetime.utcnow()).isoformat(),
                run_id,
            ],
        )

    def get_run(self, run_id: str) -> dict | None:
        db = self._get_db()
        rows = db.fetchall(
            "SELECT * FROM agent_runs WHERE id = ?", [run_id]
        )
        if not rows:
            return None
        # DuckDB returns tuples; build dict from column names
        cols = [
            "id", "user_id", "agent_name", "params", "status",
            "trace", "citations", "output", "total_input_tokens",
            "total_output_tokens", "cost_usd", "started_at", "completed_at",
            "error_message",
        ]
        return dict(zip(cols, rows[0]))

    def list_runs(
        self,
        user_id: str,
        agent_name: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        db = self._get_db()
        conditions = ["user_id = ?"]
        params: list[Any] = [user_id]
        if agent_name:
            conditions.append("agent_name = ?")
            params.append(agent_name)
        if status:
            conditions.append("status = ?")
            params.append(status)
        params.append(limit)
        sql = (
            f"SELECT id, user_id, agent_name, status, started_at, completed_at, cost_usd "
            f"FROM agent_runs WHERE {' AND '.join(conditions)} "
            f"ORDER BY started_at DESC LIMIT ?"
        )
        rows = db.fetchall(sql, params)
        cols = ["id", "user_id", "agent_name", "status", "started_at", "completed_at", "cost_usd"]
        return [dict(zip(cols, r)) for r in rows]
