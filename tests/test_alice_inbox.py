from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from omodul.alice_inbox import InboxError, InboxStore

STAMP = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def test_inbox_create_read_archive_and_projection(tmp_path: Path) -> None:
    store = InboxStore(tmp_path / "inbox.jsonl")
    report = store.create(workspace_id="ws-btc", kind="report", title="Daily BTC", summary="Sourced summary", item_id="report-1", created_at=STAMP)
    store.create(workspace_id="ws-macro", kind="question", title="CPI follow-up", summary="Needs an answer", item_id="question-1", created_at=STAMP)
    read = store.mark_read(report.item_id, updated_at=STAMP)
    assert read.state == "read"
    assert len(store.list_items(state="unread")) == 1
    assert store.list_items(workspace_id="ws-btc")[0].item_id == "report-1"
    archived = store.archive(report.item_id, updated_at=STAMP)
    assert archived.state == "archived"
    assert store.list_items(state="archived")[0].item_id == report.item_id
    with pytest.raises(InboxError, match="terminal"):
        store.mark_read(report.item_id)
    assert len(store.path.read_text(encoding="utf-8").splitlines()) == 4
    assert len(store.list_items(kind="question")) == 1


def test_inbox_rejects_duplicate_ids_bad_paths_and_naive_times(tmp_path: Path) -> None:
    store = InboxStore(tmp_path / "inbox.jsonl")
    store.create(workspace_id="ws-btc", kind="update", title="Update", summary="Summary", item_id="same", created_at=STAMP)
    with pytest.raises(InboxError, match="already exists"):
        store.create(workspace_id="ws-btc", kind="update", title="Update", summary="Summary", item_id="same", created_at=STAMP)
    with pytest.raises(InboxError, match="relative path"):
        store.create(workspace_id="ws-btc", kind="report", title="Unsafe", summary="x", artifact_path="../outside.md")
    with pytest.raises(InboxError, match="timezone"):
        store.create(workspace_id="ws-btc", kind="report", title="Bad time", summary="x", created_at="2026-10-21T12:00:00")
    with pytest.raises(InboxError, match="limit"):
        store.list_items(limit=501)


def test_corrupt_journal_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "inbox.jsonl"
    path.write_text('{"not":"an event"}\n', encoding="utf-8")
    with pytest.raises(InboxError, match="schema"):
        InboxStore(path).list_items()
