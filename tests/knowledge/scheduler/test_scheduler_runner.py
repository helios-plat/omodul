"""Tests for ScheduledJobRunner."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omodul.knowledge.scheduler.notifier import Notifier
from omodul.knowledge.scheduler.runner import ScheduledJobRunner


def _make_store_and_runner(tmp_path: Path):
    from oprim.meta_db import MetaDB
    from omodul.knowledge.scheduler.job_store import JobStore
    db_path = tmp_path / "test.duckdb"
    db = MetaDB(db_path)
    migrations_dir = (
        Path(__file__).parent.parent.parent.parent.parent.parent
        / "oprim" / "oprim" / "meta_db" / "migrations"
    )
    if migrations_dir.exists():
        db.migrate(migrations_dir)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
                agent_name TEXT NOT NULL, agent_params TEXT NOT NULL DEFAULT '{}',
                cron_expression TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                notify_on_completion BOOLEAN NOT NULL DEFAULT TRUE,
                notify_on_failure BOOLEAN NOT NULL DEFAULT TRUE,
                max_runtime_seconds INTEGER NOT NULL DEFAULT 1800,
                created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_job_runs (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, agent_run_id TEXT,
                status TEXT NOT NULL, started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP, error_message TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, agent_name TEXT NOT NULL,
                params TEXT NOT NULL, status TEXT NOT NULL, trace TEXT, citations TEXT,
                output TEXT, total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0.0,
                started_at TIMESTAMP NOT NULL, completed_at TIMESTAMP, error_message TEXT
            )
        """)
    store = JobStore(db=db)
    notifier = Notifier()
    runner = ScheduledJobRunner(job_store=store, notifier=notifier)
    # Inject same db into agent tracer
    runner._agent_runner.tracer._db = db
    return store, runner


class TestScheduledJobRunner:
    @pytest.mark.asyncio
    async def test_run_nonexistent_job_is_noop(self, tmp_path):
        store, runner = _make_store_and_runner(tmp_path)
        # Should not raise
        await runner.run("nonexistent-job-id")

    @pytest.mark.asyncio
    async def test_run_successful_job(self, tmp_path):
        from omodul.knowledge.agents.base import AgentResult

        store, runner = _make_store_and_runner(tmp_path)
        job = store.create({
            "user_id": "u1",
            "name": "test_run_job",
            "agent_name": "lint_bot",
            "cron_expression": "0 7 * * 1",
            "notify_on_completion": False,
            "notify_on_failure": False,
        })

        fake_result = AgentResult(
            success=True,
            output={"issues_count": 0, "issues": []},
            trace=[],
            citations=[],
        )

        with patch.object(runner._agent_runner, "run", new=AsyncMock(return_value=fake_result)):
            await runner.run(job["id"])

        runs = store.list_runs(job["id"])
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_run_failed_job_records_error(self, tmp_path):
        store, runner = _make_store_and_runner(tmp_path)
        job = store.create({
            "user_id": "u1",
            "name": "fail_job",
            "agent_name": "lint_bot",
            "cron_expression": "0 7 * * 1",
            "notify_on_completion": False,
            "notify_on_failure": False,
        })

        with patch.object(
            runner._agent_runner, "run",
            new=AsyncMock(side_effect=RuntimeError("agent exploded"))
        ):
            await runner.run(job["id"])

        runs = store.list_runs(job["id"])
        assert len(runs) == 1
        assert runs[0]["status"] == "failed"
        assert "agent exploded" in runs[0]["error_message"]

    @pytest.mark.asyncio
    async def test_run_unknown_agent_is_noop(self, tmp_path):
        store, runner = _make_store_and_runner(tmp_path)
        job = store.create({
            "user_id": "u1",
            "name": "unknown_agent_job",
            "agent_name": "no_such_agent_xyz",
            "cron_expression": "0 7 * * 1",
        })
        # Should not raise
        await runner.run(job["id"])
