"""Persistent deterministic KU health issues."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def _path(issue_dir: Path) -> Path:
    issue_dir.mkdir(parents=True, exist_ok=True)
    return issue_dir / "issues.jsonl"


def _issue_id(issue: dict) -> str:
    raw = f"{issue['rule']}:{issue['ku_id']}:{issue['detail']}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def list_open_issues(issue_dir: Path) -> list[dict]:
    path = issue_dir / "issues.jsonl"
    if not path.exists():
        return []
    latest: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            latest[item["issue_id"]] = item
    return [item for item in latest.values() if item.get("status") == "open"]


def persist_issues(issue_dir: Path, issues: list[dict]) -> Path:
    path = _path(issue_dir)
    existing: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                existing[item["issue_id"]] = item
    now = datetime.now(UTC).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        current_ids = set()
        for issue in issues:
            issue_id = _issue_id(issue)
            current_ids.add(issue_id)
            if issue_id in existing and existing[issue_id].get("status") == "open":
                continue
            record = {**issue, "issue_id": issue_id, "status": "open", "opened_at": now, "resolved_at": None}
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            existing[issue_id] = record
        for issue_id, record in existing.items():
            if record.get("status") == "open" and issue_id not in current_ids:
                record = {**record, "status": "resolved", "resolved_at": now}
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                existing[issue_id] = record
    return path


def mark_resolved(issue_dir: Path, issue_id: str) -> bool:
    path = issue_dir / "issues.jsonl"
    if not path.exists():
        return False
    records = []
    found = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("issue_id") == issue_id and item.get("status") == "open":
            item["status"] = "resolved"
            item["resolved_at"] = datetime.now(UTC).isoformat()
            found = True
        records.append(item)
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records), encoding="utf-8")
    return found
