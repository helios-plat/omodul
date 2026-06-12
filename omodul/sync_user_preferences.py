"""omodul.sync_user_preferences — Sync user preferences with conflict resolution.

Pillars: fingerprint only (去重)
Composition:
  - obase.persistence (fetch/save preferences)
  - oskill.resolve_conflict (three-way merge, depth-1)
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from obase.persistence import PgPool, read_one, write_one
from omodul._base_config import BaseConfig
from omodul._fingerprint import compute_fingerprint


class SyncPrefsConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "sync_user_preferences"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}
    _fingerprint_fields: ClassVar[set[str]] = {"user_id_hash"}

    user_id_hash: str
    user_id: str
    db_dsn: str
    preferences_table: str = "user_preferences"


class SyncPrefsInput(BaseModel):
    remote_prefs: dict[str, Any]  # incoming preferences from remote device
    conflict_strategy: str = "auto"  # "auto" | "local_wins" | "remote_wins"


class SyncPrefsFindings(BaseModel):
    merged_prefs: dict[str, Any]
    conflict_count: int
    resolution_strategy: str


async def sync_user_preferences(
    config: SyncPrefsConfig,
    input_data: SyncPrefsInput,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """Merge remote and local user preferences with conflict resolution.

    Returns status="completed" on success, status="failed" on any error (never raises).
    """
    enabled = config._enabled_pillars
    fingerprint = compute_fingerprint(config, input_data) if "fingerprint" in enabled else None
    error_info = None
    status = "completed"
    findings: SyncPrefsFindings | None = None

    try:
        pool = await PgPool.get_or_create(dsn=config.db_dsn)
        local_prefs = (
            await read_one(pool=pool, table=config.preferences_table, id=config.user_id) or {}
        )

        from oskill.resolve_conflict import resolve_conflict

        resolved = resolve_conflict(
            local_version=local_prefs,
            remote_version=input_data.remote_prefs,
            strategy=input_data.conflict_strategy,
            conflict_type="metadata",
        )

        await write_one(
            pool=pool,
            table=config.preferences_table,
            data={"user_id": config.user_id, **resolved.resolved},
            conflict_on=["user_id"],
        )

        findings = SyncPrefsFindings(
            merged_prefs=resolved.resolved,
            conflict_count=len(resolved.conflicts),
            resolution_strategy=resolved.resolution_strategy,
        )

    except Exception as e:
        error_info = {
            "error_class": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        status = "failed"

    return {
        "findings": findings,
        "status": status,
        "error": error_info,
        "fingerprint": fingerprint,
        "decision_trail": None,
        "report_path": None,
        "cost_usd": 0.0,
    }


def compute_fingerprint_for(config: SyncPrefsConfig, input_data: SyncPrefsInput) -> str:
    return compute_fingerprint(config, input_data)
