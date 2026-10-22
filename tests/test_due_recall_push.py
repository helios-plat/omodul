"""Regression tests for lazy Telegram dependency resolution in due-recall push."""

from __future__ import annotations

import pytest

from omodul.due_recall_push import (
    DueRecallPushConfig,
    DueRecallPushInput,
    due_recall_push_workflow,
)


@pytest.mark.asyncio
async def test_missing_telegram_token_is_reported_as_delivery_failure(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    result = await due_recall_push_workflow(
        DueRecallPushConfig(),
        DueRecallPushInput(
            batch_id="batch-test",
            due_items=[{"student_id": "student-test", "kc_id": "kc-test"}],
        ),
        pool=None,
    )

    assert result["status"] == "failed"
    assert result["findings"]["total_items"] == 1
    assert result["findings"]["error_count"] == 1
    assert result["decision_trail"][-1]["event"] == "push_failed"
    assert "TELEGRAM_BOT_TOKEN is not configured" in result["error"]


@pytest.mark.asyncio
async def test_empty_due_recall_batch_skips_delivery_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    result = await due_recall_push_workflow(
        DueRecallPushConfig(),
        DueRecallPushInput(batch_id="empty-batch", due_items=[]),
        pool=None,
    )

    assert result["status"] == "skipped"
    assert result["findings"]["total_items"] == 0
    assert result["findings"]["error_count"] == 0
