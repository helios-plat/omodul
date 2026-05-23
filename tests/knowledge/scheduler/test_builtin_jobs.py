"""Tests for builtin_jobs + Notifier."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from omodul.knowledge.scheduler.builtin_jobs import (
    BUILTIN_JOB_SPECS,
    install_builtin_jobs,
)
from omodul.knowledge.scheduler.notifier import Notifier


def _make_store(tmp_path: Path):
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
    return JobStore(db=db)


class TestInstallBuiltinJobs:
    def test_install_creates_all_four_jobs(self, tmp_path):
        store = _make_store(tmp_path)
        created = install_builtin_jobs("wiki", store)
        assert len(created) == 6
        names = {j["name"] for j in created}
        assert "daily_inbox_process" in names
        assert "daily_digest" in names
        assert "weekly_lint" in names
        assert "nightly_translation" in names
        assert "weekly_review" in names
        assert "monthly_review" in names

    def test_install_is_idempotent(self, tmp_path):
        store = _make_store(tmp_path)
        install_builtin_jobs("wiki", store)
        second_run = install_builtin_jobs("wiki", store)
        assert second_run == []  # nothing new created

    def test_nightly_translation_disabled_by_default(self, tmp_path):
        store = _make_store(tmp_path)
        install_builtin_jobs("wiki", store)
        job = store.find_by_name("wiki", "nightly_translation")
        assert job is not None
        assert not job["enabled"]

    def test_builtin_specs_have_valid_cron(self, tmp_path):
        from apscheduler.triggers.cron import CronTrigger
        for spec in BUILTIN_JOB_SPECS:
            # Should not raise
            trigger = CronTrigger.from_crontab(
                spec["cron_expression"], timezone=spec["timezone"]
            )
            assert trigger is not None


class TestNotifier:
    @pytest.mark.asyncio
    async def test_notify_completion_calls_dispatcher(self):
        dispatcher = AsyncMock()
        dispatcher.push = AsyncMock()
        notifier = Notifier(dispatcher=dispatcher)
        job = {"user_id": "u1", "name": "test_job"}
        await notifier.notify_completion(job, {"done": True})
        dispatcher.push.assert_called_once()
        args = dispatcher.push.call_args.kwargs
        assert args["user_id"] == "u1"
        assert "test_job" in args["title"]

    @pytest.mark.asyncio
    async def test_notify_failure_calls_dispatcher(self):
        dispatcher = AsyncMock()
        dispatcher.push = AsyncMock()
        notifier = Notifier(dispatcher=dispatcher)
        job = {"user_id": "u1", "name": "test_job"}
        await notifier.notify_failure(job, "oops")
        dispatcher.push.assert_called_once()
        args = dispatcher.push.call_args.kwargs
        assert "✗" in args["title"]

    @pytest.mark.asyncio
    async def test_notify_no_dispatcher_noop(self):
        notifier = Notifier(dispatcher=None)
        await notifier.notify_completion({"user_id": "u1", "name": "j"}, {})
        await notifier.notify_failure({"user_id": "u1", "name": "j"}, "err")
        # No exception raised
