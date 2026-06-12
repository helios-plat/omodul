"""Tests for autoheal_cycle omodul (batch-3 obase.persistence + obase.docker migration)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

from omodul.autoheal_cycle import (  # noqa: E402
    AutohealCycleConfig,
    AutohealCycleInput,
    autoheal_cycle,
)


def _make_config() -> AutohealCycleConfig:
    return AutohealCycleConfig(
        cycle_id="cycle-001",
        db_dsn="postgresql://user:pass@localhost/aegis",
        max_alerts_per_run=5,
        restart_timeout_sec=10,
        verify_timeout_sec=15,
    )


def _make_input() -> AutohealCycleInput:
    return AutohealCycleInput(docker_host="unix:///var/run/docker.sock")


def _make_verify_result(healthy: bool = True) -> MagicMock:
    r = MagicMock()
    r.healthy = healthy
    r.detail = "ok" if healthy else "container not running"
    return r


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.verify_health_after_action")
@patch("omodul.autoheal_cycle.docker_container_restart")
@patch("omodul.autoheal_cycle.update_one", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.execute_query", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.PgPool.get_or_create", new_callable=AsyncMock)
async def test_autoheal_cycle_completed_status(
    mock_pool, mock_query, mock_update, mock_restart, mock_verify, mock_sleep, tmp_path
):
    mock_pool.return_value = MagicMock()
    mock_query.return_value = []  # no alerts
    mock_update.return_value = True

    config = _make_config()
    res = await autoheal_cycle(config, _make_input(), tmp_path / "out")

    assert res["status"] == "completed"
    assert res["fingerprint"] is not None


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.verify_health_after_action")
@patch("omodul.autoheal_cycle.docker_container_restart")
@patch("omodul.autoheal_cycle.update_one", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.execute_query", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.PgPool.get_or_create", new_callable=AsyncMock)
async def test_autoheal_cycle_findings_populated(
    mock_pool, mock_query, mock_update, mock_restart, mock_verify, mock_sleep, tmp_path
):
    mock_pool.return_value = MagicMock()
    mock_query.return_value = [{"id": 1, "source": "container:app"}]
    mock_restart.return_value = None
    mock_verify.return_value = _make_verify_result(healthy=True)
    mock_update.return_value = True

    config = _make_config()
    res = await autoheal_cycle(config, _make_input(), tmp_path / "out")

    assert res["findings"] is not None
    f = res["findings"]
    assert f["cycle_id"] == "cycle-001"
    assert f["alerts_processed"] == 1
    assert f["recovered_count"] == 1


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.verify_health_after_action")
@patch("omodul.autoheal_cycle.docker_container_restart")
@patch("omodul.autoheal_cycle.update_one", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.execute_query", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.PgPool.get_or_create", new_callable=AsyncMock)
async def test_autoheal_cycle_skips_non_container_alerts(
    mock_pool, mock_query, mock_update, mock_restart, mock_verify, mock_sleep, tmp_path
):
    mock_pool.return_value = MagicMock()
    mock_query.return_value = [
        {"id": 1, "source": "node:10.0.0.1"},  # node-level, should be skipped
        {"id": 2, "source": "container:app"},   # container, should restart
    ]
    mock_restart.return_value = None
    mock_verify.return_value = _make_verify_result(healthy=True)
    mock_update.return_value = True

    config = _make_config()
    res = await autoheal_cycle(config, _make_input(), tmp_path / "out")

    assert res["status"] == "completed"
    f = res["findings"]
    skipped = [a for a in f["actions"] if a["action_taken"] == "skipped"]
    restarted = [a for a in f["actions"] if a["action_taken"] == "restart"]
    assert len(skipped) == 1
    assert len(restarted) == 1


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.verify_health_after_action")
@patch("omodul.autoheal_cycle.docker_container_restart")
@patch("omodul.autoheal_cycle.update_one", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.execute_query", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.PgPool.get_or_create", new_callable=AsyncMock)
async def test_autoheal_cycle_mark_handled_called_per_alert(
    mock_pool, mock_query, mock_update, mock_restart, mock_verify, mock_sleep, tmp_path
):
    mock_pool.return_value = MagicMock()
    alerts = [{"id": 10, "source": "container:a"}, {"id": 20, "source": "container:b"}]
    mock_query.return_value = alerts
    mock_restart.return_value = None
    mock_verify.return_value = _make_verify_result(healthy=True)
    mock_update.return_value = True

    config = _make_config()
    await autoheal_cycle(config, _make_input(), tmp_path / "out")

    assert mock_update.call_count == 2
    ids_updated = {call.kwargs.get("id") for call in mock_update.call_args_list}
    assert ids_updated == {10, 20}


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.verify_health_after_action")
@patch("omodul.autoheal_cycle.docker_container_restart")
@patch("omodul.autoheal_cycle.update_one", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.execute_query", new_callable=AsyncMock)
@patch("omodul.autoheal_cycle.PgPool.get_or_create", new_callable=AsyncMock)
async def test_autoheal_cycle_query_called_with_limit(
    mock_pool, mock_query, mock_update, mock_restart, mock_verify, mock_sleep, tmp_path
):
    mock_pool.return_value = MagicMock()
    mock_query.return_value = []
    mock_update.return_value = True

    config = _make_config()
    config = AutohealCycleConfig(
        cycle_id="cycle-001",
        db_dsn="postgresql://user:pass@localhost/aegis",
        max_alerts_per_run=7,
    )
    await autoheal_cycle(config, _make_input(), tmp_path / "out")

    mock_query.assert_called_once()
    call_kwargs = mock_query.call_args.kwargs
    assert call_kwargs.get("limit") == 7
