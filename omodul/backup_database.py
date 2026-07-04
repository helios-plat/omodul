"""backup_database — 平台自身 Postgres 库的备份/恢复 (aegis DESIGN §11.4 自身可恢复).

区别于 backup_app_data(应用数据域):本模块 pg_dump/pg_restore 平台自身库(承载全部策略/配置/
历史)。§11 要求一次真实 restore 演练——未演练的备份等于没有备份。遵 omodul 重量级 module
契约(BaseConfig + fingerprint + cost_tracker + decision_trail + report + stages)。
"""

import hashlib
import json
import subprocess
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class BackupDatabaseConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "backup_database"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {
        "instance_name",
        "backup_target",
        "pg_dump_format",
    }
    instance_name: str
    backup_target: str  # file:///mnt/backups/ 或 s3://bucket/path/
    pg_dump_format: Literal["custom", "plain"] = "custom"


class BackupDatabaseInput(BaseModel):
    dsn: str  # 连接串(敏感,不入 fingerprint)
    database_name: str


class BackupDatabaseFindings(BaseModel):
    backup_id: str
    artifact_path: str
    size_bytes: int
    checksum_sha256: str


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_database(
    config: BackupDatabaseConfig,
    input_data: BackupDatabaseInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """pg_dump 平台自身库,产出可恢复的 dump 工件(含 sha256)。"""
    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    token = _current_cost_tracker.set(cost_tracker)
    try:
        dump_info = _stage_dump(config, input_data, output_dir, trail_steps, on_step)
        findings = BackupDatabaseFindings(
            backup_id=f"{fingerprint[:16]}_{int(started_at.timestamp())}",
            artifact_path=dump_info["path"],
            size_bytes=dump_info["size"],
            checksum_sha256=dump_info["checksum"],
        )
    except Exception as e:
        error_info = {
            "error_class": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        status = "failed"
    finally:
        _current_cost_tracker.reset(token)

    decision_trail = build_decision_trail(
        fingerprint=fingerprint,
        config=config,
        input_data=input_data,
        trail_steps=trail_steps,
        cost_tracker=cost_tracker,
        started_at=started_at,
        status=status,
        error=error_info,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "decision_trail.json").write_text(
        json.dumps(decision_trail, indent=2, ensure_ascii=False, default=str)
    )

    report_path = write_markdown_report(
        output_dir=output_dir,
        omodul_name=config._omodul_name,
        fingerprint=fingerprint,
        config=config,
        findings=findings,
        decision_trail=decision_trail,
        cost_tracker=cost_tracker,
        status=status,
    )

    return {
        "findings": findings,
        "fingerprint": fingerprint,
        "decision_trail": decision_trail,
        "report_path": report_path,
        "cost_usd": cost_tracker.total_usd,
        "status": status,
        "error": error_info,
    }


def _stage_dump(
    config: BackupDatabaseConfig,
    input_data: BackupDatabaseInput,
    output_dir: Path,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    step_start = datetime.now(UTC)
    ext = "dump" if config.pg_dump_format == "custom" else "sql"
    target = output_dir / "staging" / f"{input_data.database_name}.{ext}"
    target.parent.mkdir(parents=True, exist_ok=True)

    fmt_flag = "-Fc" if config.pg_dump_format == "custom" else "-Fp"
    cmd = ["pg_dump", fmt_flag, "-f", str(target), "--dbname", input_data.dsn]
    result = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {result.stderr[:500]}")
    if not target.exists():
        raise RuntimeError(f"pg_dump reported success but produced no artifact at {target}")

    size = target.stat().st_size
    checksum = _sha256_file(target)
    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="omodul",
        callable_name="pg_dump",
        inputs_summary={"database": input_data.database_name, "format": config.pg_dump_format},
        outputs_summary={"size_bytes": size, "status": "dumped"},
        started_at=step_start,
    )
    return {"path": str(target), "size": size, "checksum": checksum}


def restore_database(
    *,
    artifact_path: str,
    dsn: str,
    pg_dump_format: Literal["custom", "plain"] = "custom",
) -> None:
    """从 dump 工件恢复到目标库(pg_restore / psql)。§11 restore 演练用。

    Raises:
        FileNotFoundError: 工件不存在。
        RuntimeError: 恢复命令非零退出。
    """
    p = Path(artifact_path)
    if not p.is_file():
        raise FileNotFoundError(f"restore artifact not found: {artifact_path}")

    if pg_dump_format == "custom":
        cmd = ["pg_restore", "--clean", "--if-exists", "--dbname", dsn, str(p)]
    else:
        cmd = ["psql", "--dbname", dsn, "-f", str(p)]

    result = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"restore failed (exit {result.returncode}): {result.stderr[:500]}")
