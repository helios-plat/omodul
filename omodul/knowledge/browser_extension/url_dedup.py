"""URL deduplication for browser extension ingestion."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from oprim.meta_db import open_meta_db
from oprim.meta_db.duckdb import MetaDB

from oskill.knowledge._context import meta_db_path

import oprim.meta_db as _oprim_meta_db_mod
from pathlib import Path

_MIGRATIONS_DIR = Path(_oprim_meta_db_mod.__file__).parent / "migrations"

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "twclid", "ref", "_hsenc",
})


def normalize_url(url: str) -> str:
    """Strip tracking params and fragment; lowercase scheme+host."""
    try:
        p = urlparse(url)
        clean_query = urlencode([
            (k, v) for k, v in parse_qsl(p.query)
            if k not in _TRACKING_PARAMS
        ])
        return urlunparse((
            p.scheme.lower(), p.netloc.lower(), p.path,
            p.params, clean_query, "",  # drop fragment
        ))
    except Exception:
        return url


def _get_db() -> MetaDB:
    db_path = meta_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = open_meta_db(db_path)
    db.migrate(_MIGRATIONS_DIR)
    return db


def check_url_existing(url: str) -> str | None:
    """Return substrate_id if URL was already ingested, else None."""
    normalized = normalize_url(url)
    db = _get_db()
    rows = db.fetchall(
        "SELECT substrate_id FROM browser_ext_url_index WHERE normalized_url = ?",
        [normalized],
    )
    if rows:
        return rows[0][0]
    return None


def mark_url_ingested(url: str, substrate_id: str) -> None:
    """Record URL → substrate_id mapping."""
    import uuid
    normalized = normalize_url(url)
    db = _get_db()
    db.execute(
        "INSERT OR IGNORE INTO browser_ext_url_index "
        "(id, url, normalized_url, substrate_id) VALUES (?, ?, ?, ?)",
        [str(uuid.uuid4()), url, normalized, substrate_id],
    )
