"""omodul.export_user_data_csv — Export user data to CSV file.

Pillars: fingerprint, report
Composition:
  - oprim.db_query (fetch user data)
  - oprim.csv_writer (write to CSV file)
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._fingerprint import compute_fingerprint


class ExportUserDataConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "export_user_data_csv"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "report"}
    _fingerprint_fields: ClassVar[set[str]] = {"user_id_hash", "export_scope"}

    user_id_hash: str
    user_id: str
    db_dsn: str
    export_scope: str = "all"  # "all" | "substrates" | "notes" | "preferences"
    output_filename: str = "user_export.csv"


class ExportUserDataInput(BaseModel):
    custom_query: str | None = None  # override default query


class ExportUserDataFindings(BaseModel):
    csv_path: str
    row_count: int
    export_scope: str


def export_user_data_csv(
    config: ExportUserDataConfig,
    input_data: ExportUserDataInput,
    output_dir: Path,
) -> dict[str, Any]:
    """Export user data to CSV.

    Internal oprim composition:
      - oprim.db_query — fetch user data
      - oprim.csv_writer — write to CSV file

    Returns status="completed" on success, status="failed" on any error (never raises).
    """
    enabled = config._enabled_pillars
    fingerprint = compute_fingerprint(config, input_data) if "fingerprint" in enabled else None
    error_info = None
    status = "completed"
    findings: ExportUserDataFindings | None = None

    try:
        from oprim.db_query import db_query

        query = input_data.custom_query or _build_query(config.export_scope)
        rows = db_query(
            dsn=config.db_dsn, query=query, params={"user_id": config.user_id}, limit=10000
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        csv_path = output_dir / config.output_filename

        from oprim.csv_writer import csv_writer  # type: ignore[import-not-found]

        written_path = csv_writer(data=rows, output_path=csv_path)

        findings = ExportUserDataFindings(
            csv_path=str(written_path),
            row_count=len(rows),
            export_scope=config.export_scope,
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


def _build_query(scope: str) -> str:
    if scope == "substrates":
        return "SELECT * FROM substrates WHERE user_id = %(user_id)s"
    elif scope == "notes":
        return "SELECT * FROM notes WHERE user_id = %(user_id)s"
    elif scope == "preferences":
        return "SELECT * FROM user_preferences WHERE user_id = %(user_id)s"
    return "SELECT * FROM user_data WHERE user_id = %(user_id)s"


def compute_fingerprint_for(config: ExportUserDataConfig, input_data: ExportUserDataInput) -> str:
    return compute_fingerprint(config, input_data)
