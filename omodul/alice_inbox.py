"""Append-only local Inbox for research reports, questions, and update summaries."""
from __future__ import annotations

import fcntl
import json
import os
import re
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

InboxKind = Literal["report", "question", "update"]
InboxState = Literal["unread", "read", "archived"]
_KINDS = {"report", "question", "update"}
_STATES = {"unread", "read", "archived"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class InboxError(ValueError):
    """Invalid Inbox content or event journal."""


def _timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        moment = datetime.now(UTC)
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError as exc:
            raise InboxError("timestamp must be an ISO-8601 datetime") from exc
    else:
        moment = value
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise InboxError("timestamps must include a timezone")
    return moment.astimezone(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class InboxItem:
    item_id: str
    workspace_id: str
    kind: InboxKind
    title: str
    summary: str
    created_at: str
    state: InboxState = "unread"
    updated_at: str | None = None
    artifact_path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not _ID.fullmatch(self.item_id):
            raise InboxError("item_id must be a safe identifier")
        if not isinstance(self.workspace_id, str) or not _ID.fullmatch(self.workspace_id):
            raise InboxError("workspace_id must be a safe identifier")
        if self.kind not in _KINDS:
            raise InboxError("kind must be report, question, or update")
        if not isinstance(self.title, str) or not self.title.strip() or len(self.title) > 300:
            raise InboxError("title must contain 1..300 characters")
        if not isinstance(self.summary, str) or not self.summary.strip() or len(self.summary) > 12000:
            raise InboxError("summary must contain 1..12000 characters")
        if self.state not in _STATES:
            raise InboxError("state must be unread, read, or archived")
        _timestamp(self.created_at)
        if self.updated_at is not None:
            _timestamp(self.updated_at)
        if self.artifact_path is not None:
            path = Path(self.artifact_path)
            if path.is_absolute() or any(part in {"..", "."} for part in path.parts) or "\\" in self.artifact_path:
                raise InboxError("artifact_path must be a safe relative path")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "item_id": self.item_id,
            "workspace_id": self.workspace_id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "created_at": self.created_at,
            "state": self.state,
            "updated_at": self.updated_at,
            "artifact_path": self.artifact_path,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "InboxItem":
        return cls(**value)  # type: ignore[arg-type]


class InboxStore:
    """Filesystem Inbox whose JSONL lines are full immutable item-state snapshots."""

    schema_version = 1
    _thread_lock = threading.RLock()

    def __init__(self, path: str | Path) -> None:
        requested = Path(path).expanduser()
        self.path = requested.parent.resolve() / requested.name

    def _open_exclusive(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise InboxError("Inbox journal must not be a symbolic link")
        return self.path.open("a+b")

    def _append_stream(self, stream, item: InboxItem) -> None:
        event = {"schema_version": self.schema_version, "item": item.to_dict()}
        stream.seek(0, os.SEEK_END)
        stream.write((json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())

    def _load_stream(self, stream) -> dict[str, InboxItem]:
        stream.seek(0)
        raw = stream.read()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InboxError("Inbox journal is not UTF-8") from exc
        latest: dict[str, InboxItem] = {}
        for line_no, line in enumerate(content.splitlines(), 1):
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("event must be an object")
                if event.get("schema_version") != self.schema_version:
                    raise InboxError(f"unsupported Inbox schema at line {line_no}")
                item = InboxItem.from_dict(event["item"])
            except InboxError:
                raise
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
                raise InboxError(f"invalid Inbox journal line {line_no}") from exc
            latest[item.item_id] = item
        return latest

    def _load_unlocked(self) -> dict[str, InboxItem]:
        if not self.path.exists():
            return {}
        if self.path.is_symlink():
            raise InboxError("Inbox journal must not be a symbolic link")
        with self.path.open("rb") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            try:
                return self._load_stream(stream)
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def create(
        self,
        *,
        workspace_id: str,
        kind: InboxKind,
        title: str,
        summary: str,
        item_id: str | None = None,
        artifact_path: str | None = None,
        created_at: datetime | str | None = None,
    ) -> InboxItem:
        item = InboxItem(
            item_id=item_id or str(uuid4()),
            workspace_id=workspace_id,
            kind=kind,
            title=title,
            summary=summary,
            created_at=_timestamp(created_at),
            updated_at=None,
            artifact_path=artifact_path,
        )
        with self._thread_lock, self._open_exclusive() as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                items = self._load_stream(stream)
                if item.item_id in items:
                    raise InboxError(f"Inbox item already exists: {item.item_id}")
                self._append_stream(stream, item)
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return item

    def _transition(self, item_id: str, state: InboxState, *, updated_at: datetime | str | None = None) -> InboxItem:
        with self._thread_lock, self._open_exclusive() as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                item = self._load_stream(stream).get(item_id)
                if item is None:
                    raise InboxError(f"unknown Inbox item: {item_id}")
                if item.state == "archived":
                    raise InboxError("archived Inbox items are terminal")
                if item.state == state:
                    return item
                updated = replace(item, state=state, updated_at=_timestamp(updated_at))
                self._append_stream(stream, updated)
                return updated
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def mark_read(self, item_id: str, *, updated_at: datetime | str | None = None) -> InboxItem:
        return self._transition(item_id, "read", updated_at=updated_at)

    def archive(self, item_id: str, *, updated_at: datetime | str | None = None) -> InboxItem:
        return self._transition(item_id, "archived", updated_at=updated_at)

    def list_items(
        self,
        *,
        state: InboxState | None = None,
        workspace_id: str | None = None,
        kind: InboxKind | None = None,
        limit: int = 100,
    ) -> list[InboxItem]:
        if state is not None and state not in _STATES:
            raise InboxError("invalid Inbox state filter")
        if kind is not None and kind not in _KINDS:
            raise InboxError("invalid Inbox kind filter")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise InboxError("limit must be an integer in [1, 500]")
        items = self._load_unlocked().values()
        filtered = [item for item in items if (state is None or item.state == state) and (workspace_id is None or item.workspace_id == workspace_id) and (kind is None or item.kind == kind)]
        return sorted(filtered, key=lambda item: (item.created_at, item.item_id), reverse=True)[:limit]
