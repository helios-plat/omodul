import sys
from unittest.mock import MagicMock, patch

sys.modules["docker"] = MagicMock()
sys.modules["docker.errors"] = MagicMock()

from pathlib import Path

import pytest

from omodul.backup_database import (
    BackupDatabaseConfig,
    BackupDatabaseInput,
    backup_database,
    restore_database,
)

_DSN = "postgresql://u:p@h:5432/aegis"


def _cfg(**over):
    base = {"instance_name": "aegis-prod", "backup_target": "file:///mnt/backups/"}
    base.update(over)
    return BackupDatabaseConfig(**base)


def _inp():
    return BackupDatabaseInput(dsn=_DSN, database_name="aegis")


def _fake_pg_dump_ok(cmd, **kw):
    # cmd = ["pg_dump", "-Fc", "-f", <target>, "--dbname", dsn]; 造出工件供 size/sha256
    Path(cmd[3]).write_bytes(b"PGDMP fake dump content")
    return MagicMock(returncode=0, stderr="")


class TestBackupDatabase:
    def test_completed_normal_path(self, tmp_path):
        with patch("omodul.backup_database.subprocess.run", side_effect=_fake_pg_dump_ok):
            result = backup_database(_cfg(), _inp(), tmp_path)
        assert result["status"] == "completed"
        f = result["findings"]
        assert f.size_bytes > 0
        assert len(f.checksum_sha256) == 64
        assert f.artifact_path.endswith("aegis.dump")

    def test_plain_format_produces_sql(self, tmp_path):
        with patch("omodul.backup_database.subprocess.run", side_effect=_fake_pg_dump_ok):
            result = backup_database(_cfg(pg_dump_format="plain"), _inp(), tmp_path)
        assert result["findings"].artifact_path.endswith("aegis.sql")

    def test_pg_dump_failure_marks_failed(self, tmp_path):
        with patch(
            "omodul.backup_database.subprocess.run",
            return_value=MagicMock(returncode=1, stderr="connection refused"),
        ):
            result = backup_database(_cfg(), _inp(), tmp_path)
        assert result["status"] == "failed"
        assert "pg_dump failed" in result["error"]["error_message"]

    def test_decision_trail_recorded(self, tmp_path):
        with patch("omodul.backup_database.subprocess.run", side_effect=_fake_pg_dump_ok):
            result = backup_database(_cfg(), _inp(), tmp_path)
        assert len(result["decision_trail"]["steps"]) >= 1

    def test_fingerprint_stable(self, tmp_path):
        with patch("omodul.backup_database.subprocess.run", side_effect=_fake_pg_dump_ok):
            r1 = backup_database(_cfg(), _inp(), tmp_path / "a")
            r2 = backup_database(_cfg(), _inp(), tmp_path / "b")
        assert r1["fingerprint"] == r2["fingerprint"]


class TestRestoreDatabase:
    def test_restore_success(self, tmp_path):
        artifact = tmp_path / "aegis.dump"
        artifact.write_bytes(b"dump")
        with patch(
            "omodul.backup_database.subprocess.run",
            return_value=MagicMock(returncode=0, stderr=""),
        ):
            restore_database(artifact_path=str(artifact), dsn=_DSN)  # 不抛即通过

    def test_restore_missing_artifact(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            restore_database(artifact_path=str(tmp_path / "nope.dump"), dsn=_DSN)

    def test_restore_failure_raises(self, tmp_path):
        artifact = tmp_path / "aegis.dump"
        artifact.write_bytes(b"dump")
        with patch(
            "omodul.backup_database.subprocess.run",
            return_value=MagicMock(returncode=1, stderr="bad restore"),
        ):
            with pytest.raises(RuntimeError, match="restore failed"):
                restore_database(artifact_path=str(artifact), dsn=_DSN)
