"""Built-in scheduled job definitions for Stratum.

Call `install_builtin_jobs(user_id, job_store)` on first run to seed the jobs.
"""
from __future__ import annotations

from .job_store import JobStore

BUILTIN_JOB_SPECS = [
    {
        "name": "daily_inbox_process",
        "agent_name": "knowledge_curator",
        "cron_expression": "0 6 * * *",
        "timezone": "Asia/Shanghai",
        "agent_params": {"inbox_dir": "~/.stratum/inbox"},
        "enabled": True,
    },
    {
        "name": "daily_digest",
        "agent_name": "daily_digest",
        "cron_expression": "0 8 * * *",
        "timezone": "Asia/Shanghai",
        "agent_params": {},
        "enabled": True,
    },
    {
        "name": "weekly_lint",
        "agent_name": "lint_bot",
        "cron_expression": "0 7 * * 1",
        "timezone": "Asia/Shanghai",
        "agent_params": {},
        "enabled": True,
    },
    {
        "name": "nightly_translation",
        "agent_name": "translation_worker",
        "cron_expression": "0 2 * * *",
        "timezone": "Asia/Shanghai",
        "agent_params": {"max_substrates": 5},
        "enabled": False,  # user must explicitly enable
    },
    {
        "name": "weekly_review",
        "agent_name": "daily_digest",
        "cron_expression": "0 9 * * 0",
        "timezone": "Asia/Shanghai",
        "agent_params": {"time_range": "last_7_days", "title_prefix": "周回顾"},
        "enabled": False,
    },
    {
        "name": "monthly_review",
        "agent_name": "daily_digest",
        "cron_expression": "0 9 1 * *",
    {
        "name": "nightly_audio_gen",
        "agent_name": "audio_generator",
        "cron_expression": "0 3 * * *",
        "timezone": "Asia/Shanghai",
        "agent_params": {"max_substrates": 5},
        "enabled": False,
    },
        "timezone": "Asia/Shanghai",
        "agent_params": {"time_range": "last_30_days", "title_prefix": "月回顾"},
        "enabled": False,
        "max_runtime_seconds": 3600,
    },
]


def install_builtin_jobs(user_id: str, job_store: JobStore) -> list[dict]:
    """Create builtin jobs for *user_id* if they don't already exist.

    Idempotent — skips jobs that are already present (by name).
    Returns list of created job dicts.
    """
    created: list[dict] = []
    for spec in BUILTIN_JOB_SPECS:
        existing = job_store.find_by_name(user_id, spec["name"])
        if existing:
            continue
        job = job_store.create({**spec, "user_id": user_id})
        created.append(job)
    return created
