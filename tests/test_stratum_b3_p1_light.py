"""Tests for omodul-006/027/028: notification_dispatch_workflow, export_user_data_csv, sync_user_preferences."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub missing deps not installed in omodul venv.
# jinja2/sqlalchemy/frontmatter must come first so oskill (which is a real
# installed package) can be imported without errors. oskill itself must NOT
# be stubbed: omodul/__init__.py chain imports triage_signal which does
# `from oskill import Signal` at module level; a MagicMock Signal causes
# Pydantic schema generation to fail.
# oprim.csv_writer is a not-yet-existing oprim module — stub it so patch()
# can resolve "oprim.csv_writer.csv_writer" during tests.
_STUB_MODULES = [
    "jinja2",
    "alembic",
    "alembic.command",
    "alembic.config",
    "alembic.runtime",
    "alembic.runtime.migration",
    "alembic.script",
    "sqlalchemy",
    "frontmatter",
]
for _mod in _STUB_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Stub oprim.csv_writer (does not exist yet in oprim).
if "oprim.csv_writer" not in sys.modules:
    _csv_writer_mod = MagicMock()
    sys.modules["oprim.csv_writer"] = _csv_writer_mod

from omodul.notification_dispatch_workflow import (  # noqa: E402
    NotifDispatchConfig,
    NotifDispatchInput,
    compute_fingerprint_for as notif_fingerprint_for,
    notification_dispatch_workflow,
)
from omodul.export_user_data_csv import (  # noqa: E402
    ExportUserDataConfig,
    ExportUserDataInput,
    compute_fingerprint_for as export_fingerprint_for,
    export_user_data_csv,
)
from omodul.sync_user_preferences import (  # noqa: E402
    SyncPrefsConfig,
    SyncPrefsInput,
    sync_user_preferences,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _email_result(success: bool = True):
    from oprim.push_email import EmailResult

    return EmailResult(success=success, to="user@example.com", subject="test")


def _notif_config(
    channel: str = "email", to_address: str | None = "user@example.com", **kw
) -> NotifDispatchConfig:
    return NotifDispatchConfig(
        user_id_hash="hash-abc",
        notification_type="marketing",
        channel=channel,  # type: ignore[arg-type]
        to_address=to_address,
        **kw,
    )


def _notif_input(**kw) -> NotifDispatchInput:
    return NotifDispatchInput(subject="Hello!", **kw)


def _export_config(**kw) -> ExportUserDataConfig:
    return ExportUserDataConfig(
        user_id_hash="hash-abc",
        user_id="user-1",
        db_dsn="postgresql://localhost/test",
        **kw,
    )


def _sync_config(**kw) -> SyncPrefsConfig:
    return SyncPrefsConfig(
        user_id_hash="hash-abc",
        user_id="user-1",
        db_dsn="postgresql://localhost/test",
        **kw,
    )


def _resolved_mock(merged: dict, conflicts: list, strategy: str = "auto"):
    m = MagicMock()
    m.resolved = merged
    m.conflicts = conflicts
    m.resolution_strategy = strategy
    return m


def _mock_pool():
    return AsyncMock(return_value=MagicMock())


# ---------------------------------------------------------------------------
# omodul-006: notification_dispatch_workflow
# ---------------------------------------------------------------------------


class TestNotifDispatchWorkflow:
    def test_email_channel_success(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="rendered body"),
            patch("oprim.push_email.push_email", return_value=_email_result(True)),
        ):
            result = notification_dispatch_workflow(_notif_config(), _notif_input(), tmp_path)
        assert result["status"] == "completed"
        assert result["findings"].sent is True
        assert result["findings"].channel == "email"
        assert result["findings"].notification_type == "marketing"

    def test_web_channel_no_crash(self, tmp_path):
        with patch("oprim.template_render.template_render", return_value="rendered body"):
            result = notification_dispatch_workflow(
                _notif_config(channel="web", to_address=None),
                _notif_input(),
                tmp_path,
            )
        assert result["status"] == "completed"
        assert result["findings"].sent is True
        assert result["findings"].channel == "web"

    def test_fingerprint_non_null(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = notification_dispatch_workflow(_notif_config(), _notif_input(), tmp_path)
        assert result["fingerprint"] is not None
        assert len(result["fingerprint"]) == 64

    def test_smtp_failure_status_failed(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", side_effect=OSError("connection refused")),
        ):
            result = notification_dispatch_workflow(_notif_config(), _notif_input(), tmp_path)
        assert result["status"] == "failed"
        assert result["error"] is not None
        assert result["findings"].sent is False

    def test_compute_fingerprint_for_deterministic(self):
        config = _notif_config()
        inp = _notif_input()
        fp1 = notif_fingerprint_for(config, inp)
        fp2 = notif_fingerprint_for(config, inp)
        assert fp1 == fp2
        assert len(fp1) == 64

    def test_wechat_channel_no_crash(self, tmp_path):
        with patch("oprim.template_render.template_render", return_value="body"):
            result = notification_dispatch_workflow(
                _notif_config(channel="wechat", to_address=None),
                _notif_input(),
                tmp_path,
            )
        assert result["status"] == "completed"

    def test_cost_is_zero(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = notification_dispatch_workflow(_notif_config(), _notif_input(), tmp_path)
        assert result["cost_usd"] == 0.0

    def test_decision_trail_is_none(self, tmp_path):
        with (
            patch("oprim.template_render.template_render", return_value="body"),
            patch("oprim.push_email.push_email", return_value=_email_result()),
        ):
            result = notification_dispatch_workflow(_notif_config(), _notif_input(), tmp_path)
        assert result["decision_trail"] is None


# ---------------------------------------------------------------------------
# omodul-027: export_user_data_csv
# ---------------------------------------------------------------------------


class TestExportUserDataCsv:
    def _fake_rows(self, n: int = 5) -> list[dict]:
        return [{"id": i, "data": f"row-{i}"} for i in range(n)]

    async def test_success_csv_path_present(self, tmp_path):
        rows = self._fake_rows(3)
        csv_out = tmp_path / "user_export.csv"
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.export_user_data_csv.query", new_callable=AsyncMock, return_value=rows),
            patch("oprim.csv_writer.csv_writer", return_value=csv_out),
        ):
            result = await export_user_data_csv(_export_config(), ExportUserDataInput(), tmp_path)
        assert result["status"] == "completed"
        assert result["findings"].csv_path == str(csv_out)

    async def test_row_count_matches(self, tmp_path):
        rows = self._fake_rows(7)
        csv_out = tmp_path / "user_export.csv"
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.export_user_data_csv.query", new_callable=AsyncMock, return_value=rows),
            patch("oprim.csv_writer.csv_writer", return_value=csv_out),
        ):
            result = await export_user_data_csv(_export_config(), ExportUserDataInput(), tmp_path)
        assert result["findings"].row_count == 7

    async def test_failure_no_raise(self, tmp_path):
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.export_user_data_csv.query", new_callable=AsyncMock, side_effect=Exception("DB error")),
        ):
            result = await export_user_data_csv(_export_config(), ExportUserDataInput(), tmp_path)
        assert result["status"] == "failed"
        assert result["error"] is not None
        assert result["findings"] is None

    async def test_fingerprint_non_null(self, tmp_path):
        rows = self._fake_rows(2)
        csv_out = tmp_path / "user_export.csv"
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.export_user_data_csv.query", new_callable=AsyncMock, return_value=rows),
            patch("oprim.csv_writer.csv_writer", return_value=csv_out),
        ):
            result = await export_user_data_csv(_export_config(), ExportUserDataInput(), tmp_path)
        assert result["fingerprint"] is not None
        assert len(result["fingerprint"]) == 64

    async def test_custom_query_passed_to_query(self, tmp_path):
        rows = self._fake_rows(1)
        csv_out = tmp_path / "user_export.csv"
        custom_q = "SELECT id FROM substrates WHERE user_id = $1 LIMIT 1"
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.export_user_data_csv.query", new_callable=AsyncMock, return_value=rows) as mock_q,
            patch("oprim.csv_writer.csv_writer", return_value=csv_out),
        ):
            await export_user_data_csv(
                _export_config(),
                ExportUserDataInput(custom_query=custom_q),
                tmp_path,
            )
        call_kwargs = mock_q.call_args
        assert (
            call_kwargs.kwargs.get("sql") == custom_q
            or custom_q in str(call_kwargs)
        )

    async def test_export_scope_substrates(self, tmp_path):
        rows = self._fake_rows(4)
        csv_out = tmp_path / "user_export.csv"
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.export_user_data_csv.query", new_callable=AsyncMock, return_value=rows) as mock_q,
            patch("oprim.csv_writer.csv_writer", return_value=csv_out),
        ):
            result = await export_user_data_csv(
                _export_config(export_scope="substrates"),
                ExportUserDataInput(),
                tmp_path,
            )
        assert result["findings"].export_scope == "substrates"
        called_sql = mock_q.call_args.kwargs.get("sql", "")
        assert "substrates" in called_sql

    async def test_cost_is_zero(self, tmp_path):
        rows = self._fake_rows(1)
        csv_out = tmp_path / "user_export.csv"
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.export_user_data_csv.query", new_callable=AsyncMock, return_value=rows),
            patch("oprim.csv_writer.csv_writer", return_value=csv_out),
        ):
            result = await export_user_data_csv(_export_config(), ExportUserDataInput(), tmp_path)
        assert result["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# omodul-028: sync_user_preferences
# ---------------------------------------------------------------------------


class TestSyncUserPreferences:
    def _patches(self, local_prefs=None, resolved_mock=None):
        if local_prefs is None:
            local_prefs = {"theme": "dark", "lang": "en"}
        if resolved_mock is None:
            resolved_mock = _resolved_mock(
                merged={"theme": "light", "lang": "en"},
                conflicts=["theme"],
                strategy="auto",
            )
        return [
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.sync_user_preferences.read_one", new_callable=AsyncMock, return_value=local_prefs),
            patch("oskill.resolve_conflict.resolve_conflict", return_value=resolved_mock),
            patch("omodul.sync_user_preferences.write_one", new_callable=AsyncMock, return_value=1),
        ]

    async def test_merge_status_completed(self, tmp_path):
        resolved = _resolved_mock({"theme": "light"}, [], "auto")
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.sync_user_preferences.read_one", new_callable=AsyncMock, return_value={"theme": "dark"}),
            patch("oskill.resolve_conflict.resolve_conflict", return_value=resolved),
            patch("omodul.sync_user_preferences.write_one", new_callable=AsyncMock, return_value=1),
        ):
            result = await sync_user_preferences(
                _sync_config(),
                SyncPrefsInput(remote_prefs={"theme": "light"}),
                tmp_path,
            )
        assert result["status"] == "completed"
        assert result["findings"].merged_prefs == {"theme": "light"}

    async def test_conflict_count_correct(self, tmp_path):
        resolved = _resolved_mock({"a": 1, "b": 2}, ["a", "b"], "auto")
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.sync_user_preferences.read_one", new_callable=AsyncMock, return_value={"a": 0, "b": 0}),
            patch("oskill.resolve_conflict.resolve_conflict", return_value=resolved),
            patch("omodul.sync_user_preferences.write_one", new_callable=AsyncMock, return_value=1),
        ):
            result = await sync_user_preferences(
                _sync_config(),
                SyncPrefsInput(remote_prefs={"a": 1, "b": 2}),
                tmp_path,
            )
        assert result["findings"].conflict_count == 2

    async def test_remote_wins_strategy(self, tmp_path):
        resolved = _resolved_mock({"theme": "light"}, ["theme"], "remote_wins")
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.sync_user_preferences.read_one", new_callable=AsyncMock, return_value={"theme": "dark"}),
            patch("oskill.resolve_conflict.resolve_conflict", return_value=resolved) as mock_rc,
            patch("omodul.sync_user_preferences.write_one", new_callable=AsyncMock, return_value=1),
        ):
            result = await sync_user_preferences(
                _sync_config(),
                SyncPrefsInput(remote_prefs={"theme": "light"}, conflict_strategy="remote_wins"),
                tmp_path,
            )
        assert result["findings"].resolution_strategy == "remote_wins"
        call_kwargs = mock_rc.call_args.kwargs
        assert call_kwargs.get("strategy") == "remote_wins"

    async def test_no_local_prefs_uses_empty_dict(self, tmp_path):
        resolved = _resolved_mock({"theme": "light"}, [], "auto")
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.sync_user_preferences.read_one", new_callable=AsyncMock, return_value=None),
            patch("oskill.resolve_conflict.resolve_conflict", return_value=resolved) as mock_rc,
            patch("omodul.sync_user_preferences.write_one", new_callable=AsyncMock, return_value=1),
        ):
            await sync_user_preferences(
                _sync_config(),
                SyncPrefsInput(remote_prefs={"theme": "light"}),
                tmp_path,
            )
        call_kwargs = mock_rc.call_args.kwargs
        assert call_kwargs.get("local_version") == {}

    async def test_failure_no_raise(self, tmp_path):
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.sync_user_preferences.read_one", new_callable=AsyncMock, side_effect=Exception("DB down")),
        ):
            result = await sync_user_preferences(
                _sync_config(),
                SyncPrefsInput(remote_prefs={"theme": "light"}),
                tmp_path,
            )
        assert result["status"] == "failed"
        assert result["error"] is not None
        assert result["findings"] is None

    async def test_fingerprint_non_null(self, tmp_path):
        resolved = _resolved_mock({"theme": "dark"}, [], "auto")
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.sync_user_preferences.read_one", new_callable=AsyncMock, return_value={"theme": "dark"}),
            patch("oskill.resolve_conflict.resolve_conflict", return_value=resolved),
            patch("omodul.sync_user_preferences.write_one", new_callable=AsyncMock, return_value=1),
        ):
            result = await sync_user_preferences(
                _sync_config(),
                SyncPrefsInput(remote_prefs={}),
                tmp_path,
            )
        assert result["fingerprint"] is not None
        assert len(result["fingerprint"]) == 64

    async def test_cost_is_zero(self, tmp_path):
        resolved = _resolved_mock({}, [], "auto")
        with (
            patch("obase.persistence.pool.PgPool.get_or_create", new_callable=AsyncMock, return_value=MagicMock()),
            patch("omodul.sync_user_preferences.read_one", new_callable=AsyncMock, return_value={}),
            patch("oskill.resolve_conflict.resolve_conflict", return_value=resolved),
            patch("omodul.sync_user_preferences.write_one", new_callable=AsyncMock, return_value=1),
        ):
            result = await sync_user_preferences(
                _sync_config(),
                SyncPrefsInput(remote_prefs={}),
                tmp_path,
            )
        assert result["cost_usd"] == 0.0
