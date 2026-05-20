"""Tests for JobStore."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from omodul.knowledge.scheduler.errors import JobNotFoundError
from omodul.knowledge.scheduler.job_store import JobStore


def _make_store(tmp_path: Path) -> JobStore:
    from oprim.meta_db import MetaDB
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
    return JobStore(db=db)


def _spec(**kwargs) -> dict:
    return {
        "user_id": "u1",
        "name": "test_job",
        "agent_name": "daily_digest",
        "cron_expression": "0 8 * * *",
        "timezone": "Asia/Shanghai",
        **kwargs,
    }


class TestJobStoreCRUD:
    def test_create_and_get(self, tmp_path):
        store = _make_store(tmp_path)
        job = store.create(_spec())
        assert job["name"] == "test_job"
        assert job["agent_name"] == "daily_digest"
        assert job["enabled"]

        fetched = store.get(job["id"])
        assert fetched["id"] == job["id"]

    def test_get_not_found_raises(self, tmp_path):
        store = _make_store(tmp_path)
        with pytest.raises(JobNotFoundError):
            store.get("no-such-id")

    def test_find_by_name(self, tmp_path):
        store = _make_store(tmp_path)
        store.create(_spec(name="job_a"))
        assert store.find_by_name("u1", "job_a") is not None
        assert store.find_by_name("u1", "job_b") is None

    def test_list_jobs(self, tmp_path):
        store = _make_store(tmp_path)
        store.create(_spec(name="j1"))
        store.create(_spec(name="j2"))
        jobs = store.list_jobs("u1")
        assert len(jobs) == 2

    def test_list_enabled_jobs(self, tmp_path):
        store = _make_store(tmp_path)
        store.create(_spec(name="enabled_job", enabled=True))
        store.create(_spec(name="disabled_job", enabled=False))
        enabled = store.list_enabled_jobs()
        names = [j["name"] for j in enabled]
        assert "enabled_job" in names
        assert "disabled_job" not in names

    def test_update_enabled(self, tmp_path):
        store = _make_store(tmp_path)
        job = store.create(_spec(enabled=True))
        updated = store.update(job["id"], {"enabled": False})
        assert not updated["enabled"]

    def test_delete(self, tmp_path):
        store = _make_store(tmp_path)
        job = store.create(_spec())
        store.delete(job["id"])
        with pytest.raises(JobNotFoundError):
            store.get(job["id"])


class TestJobStoreRuns:
    def test_create_and_update_run(self, tmp_path):
        store = _make_store(tmp_path)
        job = store.create(_spec())
        run_id = "R001"
        store.create_run(run_id, job["id"], "running", datetime.utcnow())
        store.update_run(
            run_id,
            status="completed",
            agent_run_id="AR001",
            completed_at=datetime.utcnow(),
        )
        runs = store.list_runs(job["id"])
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"
        assert runs[0]["agent_run_id"] == "AR001"

    def test_list_runs_empty(self, tmp_path):
        store = _make_store(tmp_path)
        job = store.create(_spec())
        assert store.list_runs(job["id"]) == []
