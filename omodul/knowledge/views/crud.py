"""Views CRUD — sync DuckDB operations for the views table."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import oprim.meta_db as _oprim_meta_db_mod
from oprim.meta_db import open_meta_db
from oskill.knowledge._context import meta_db_path

_MIGRATIONS_DIR = Path(_oprim_meta_db_mod.__file__).parent / "migrations"

_SELECT_COLS = (
    "id, user_id, name, description, default_filter, default_llm, "
    "default_system_prompt, icon, is_default, is_builtin, created_at, updated_at"
)


def _open_db():
    db = open_meta_db(meta_db_path())
    db.migrate(_MIGRATIONS_DIR)
    return db


def _row_to_view(row: tuple) -> dict:
    return {
        "id": row[0],
        "user_id": row[1],
        "name": row[2],
        "description": row[3],
        "default_filter": json.loads(row[4]) if row[4] else {},
        "default_llm": json.loads(row[5]) if row[5] else {},
        "default_system_prompt": row[6],
        "icon": row[7],
        "is_default": bool(row[8]),
        "is_builtin": bool(row[9]),
        "created_at": str(row[10]),
        "updated_at": str(row[11]),
    }


def create_view(user_id: str, spec: dict) -> dict:
    db = _open_db()
    view_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        f"INSERT INTO views ({_SELECT_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            view_id, user_id, spec["name"], spec.get("description"),
            json.dumps(spec.get("default_filter", {})),
            json.dumps(spec.get("default_llm", {})),
            spec.get("default_system_prompt"),
            spec.get("icon"),
            bool(spec.get("is_default", False)),
            bool(spec.get("is_builtin", False)),
            now, now,
        ],
    )
    db.close()
    return get_view(view_id)


def get_view(view_id: str) -> dict | None:
    db = _open_db()
    rows = db.fetchall(
        f"SELECT {_SELECT_COLS} FROM views WHERE id = ?", [view_id]
    )
    db.close()
    return _row_to_view(rows[0]) if rows else None


def list_views(user_id: str) -> list[dict]:
    db = _open_db()
    rows = db.fetchall(
        f"SELECT {_SELECT_COLS} FROM views "
        "WHERE user_id = ? ORDER BY is_default DESC, name ASC",
        [user_id],
    )
    db.close()
    return [_row_to_view(r) for r in rows]


def update_view(view_id: str, updates: dict) -> dict | None:
    _ALLOWED = {"name", "description", "default_filter", "default_llm",
                "default_system_prompt", "icon"}
    db = _open_db()
    now = datetime.now(timezone.utc).isoformat()
    for key, val in updates.items():
        if key not in _ALLOWED:
            continue
        if key in ("default_filter", "default_llm"):
            val = json.dumps(val)
        db.execute(
            f"UPDATE views SET {key} = ?, updated_at = ? WHERE id = ?",
            [val, now, view_id],
        )
    db.close()
    return get_view(view_id)


def delete_view(view_id: str) -> None:
    db = _open_db()
    db.execute("DELETE FROM views WHERE id = ?", [view_id])
    db.close()


def set_default(user_id: str, view_id: str) -> None:
    """Switch the user's default view (single-default constraint)."""
    db = _open_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE views SET is_default = FALSE, updated_at = ? WHERE user_id = ?",
        [now, user_id],
    )
    db.execute(
        "UPDATE views SET is_default = TRUE, updated_at = ? WHERE id = ? AND user_id = ?",
        [now, view_id, user_id],
    )
    db.close()


def get_default_view(user_id: str) -> dict | None:
    db = _open_db()
    rows = db.fetchall(
        f"SELECT {_SELECT_COLS} FROM views "
        "WHERE user_id = ? AND is_default = TRUE LIMIT 1",
        [user_id],
    )
    db.close()
    return _row_to_view(rows[0]) if rows else None
