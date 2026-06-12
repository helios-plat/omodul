"""Tests for watch_cycle omodul (batch-3 obase.persistence + obase.docker migration)."""

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

from omodul.watch_cycle import (  # noqa: E402
    WatchCycleConfig,
    WatchCycleInput,
    watch_cycle,
)


def _make_config() -> WatchCycleConfig:
    return WatchCycleConfig(
        cycle_id="watch-001",
        db_dsn="postgresql://user:pass@localhost/aegis",
        alert_cpu_threshold=85.0,
        alert_mem_threshold=90.0,
        alert_container_down=True,
    )


def _make_input(webhook: str | None = None) -> WatchCycleInput:
    return WatchCycleInput(
        docker_hosts=["unix:///var/run/docker.sock"],
        webhook_url=webhook,
    )


def _make_node(reachable: bool = True, host: str = "unix:///var/run/docker.sock") -> MagicMock:
    n = MagicMock()
    n.reachable = reachable
    n.docker_host = host
    return n


def _make_sweep_result(nodes: list[MagicMock]) -> MagicMock:
    r = MagicMock()
    r.nodes = nodes
    r.reachable_count = sum(1 for n in nodes if n.reachable)
    return r


def _make_rank_result(entries: list[MagicMock] | None = None) -> MagicMock:
    r = MagicMock()
    r.ranked = entries or []
    return r


@pytest.mark.asyncio
@patch("omodul.watch_cycle.insert_one", new_callable=AsyncMock)
@patch("omodul.watch_cycle.docker_container_list")
@patch("omodul.watch_cycle.container_resource_rank")
@patch("omodul.watch_cycle.multi_node_health_sweep")
@patch("omodul.watch_cycle.PgPool.get_or_create", new_callable=AsyncMock)
async def test_watch_cycle_completed_status(
    mock_pool, mock_sweep, mock_rank, mock_clist, mock_insert, tmp_path
):
    mock_pool.return_value = MagicMock()
    node = _make_node(reachable=True)
    mock_sweep.return_value = _make_sweep_result([node])
    mock_rank.return_value = _make_rank_result()
    mock_clist.return_value = []

    config = _make_config()
    res = await watch_cycle(config, _make_input(), tmp_path / "out")

    assert res["status"] == "completed"
    assert res["fingerprint"] is not None


@pytest.mark.asyncio
@patch("omodul.watch_cycle.insert_one", new_callable=AsyncMock)
@patch("omodul.watch_cycle.docker_container_list")
@patch("omodul.watch_cycle.container_resource_rank")
@patch("omodul.watch_cycle.multi_node_health_sweep")
@patch("omodul.watch_cycle.PgPool.get_or_create", new_callable=AsyncMock)
async def test_watch_cycle_findings_populated(
    mock_pool, mock_sweep, mock_rank, mock_clist, mock_insert, tmp_path
):
    mock_pool.return_value = MagicMock()
    node = _make_node(reachable=True)
    mock_sweep.return_value = _make_sweep_result([node])
    mock_rank.return_value = _make_rank_result()
    mock_clist.return_value = []

    config = _make_config()
    res = await watch_cycle(config, _make_input(), tmp_path / "out")

    f = res["findings"]
    assert f is not None
    assert f["cycle_id"] == "watch-001"
    assert f["scanned_nodes"] == 1
    assert isinstance(f["alerts_generated"], list)


@pytest.mark.asyncio
@patch("omodul.watch_cycle.insert_one", new_callable=AsyncMock)
@patch("omodul.watch_cycle.docker_container_list")
@patch("omodul.watch_cycle.container_resource_rank")
@patch("omodul.watch_cycle.multi_node_health_sweep")
@patch("omodul.watch_cycle.PgPool.get_or_create", new_callable=AsyncMock)
async def test_watch_cycle_no_alerts_no_insert(
    mock_pool, mock_sweep, mock_rank, mock_clist, mock_insert, tmp_path
):
    mock_pool.return_value = MagicMock()
    node = _make_node(reachable=True)
    mock_sweep.return_value = _make_sweep_result([node])
    mock_rank.return_value = _make_rank_result()
    mock_clist.return_value = []  # no containers → no down alerts

    config = _make_config()
    await watch_cycle(config, _make_input(), tmp_path / "out")

    mock_insert.assert_not_called()


@pytest.mark.asyncio
@patch("omodul.watch_cycle.insert_one", new_callable=AsyncMock)
@patch("omodul.watch_cycle.docker_container_list")
@patch("omodul.watch_cycle.container_resource_rank")
@patch("omodul.watch_cycle.multi_node_health_sweep")
@patch("omodul.watch_cycle.PgPool.get_or_create", new_callable=AsyncMock)
async def test_watch_cycle_down_container_triggers_insert(
    mock_pool, mock_sweep, mock_rank, mock_clist, mock_insert, tmp_path
):
    mock_pool.return_value = MagicMock()
    node = _make_node(reachable=True)
    mock_sweep.return_value = _make_sweep_result([node])
    mock_rank.return_value = _make_rank_result()

    down_container = MagicMock()
    down_container.state = "exited"
    down_container.name = "dead-app"
    mock_clist.return_value = [down_container]
    mock_insert.return_value = 1

    config = _make_config()
    res = await watch_cycle(config, _make_input(), tmp_path / "out")

    assert res["status"] == "completed"
    mock_insert.assert_called_once()
    call_kwargs = mock_insert.call_args.kwargs
    assert call_kwargs.get("table") == "aegis_alert_events"


@pytest.mark.asyncio
@patch("omodul.watch_cycle.http_post_webhook")
@patch("omodul.watch_cycle.insert_one", new_callable=AsyncMock)
@patch("omodul.watch_cycle.docker_container_list")
@patch("omodul.watch_cycle.container_resource_rank")
@patch("omodul.watch_cycle.multi_node_health_sweep")
@patch("omodul.watch_cycle.PgPool.get_or_create", new_callable=AsyncMock)
async def test_watch_cycle_webhook_called_for_alert(
    mock_pool, mock_sweep, mock_rank, mock_clist, mock_insert, mock_webhook, tmp_path
):
    mock_pool.return_value = MagicMock()
    # Make a node unreachable → generates a node alert
    node = _make_node(reachable=False, host="tcp://10.0.0.1:2375")
    mock_sweep.return_value = _make_sweep_result([node])
    mock_rank.return_value = _make_rank_result()
    mock_clist.return_value = []
    mock_insert.return_value = 1

    config = _make_config()
    await watch_cycle(config, _make_input(webhook="https://hooks.example.com/alert"), tmp_path / "out")

    mock_webhook.assert_called_once()
    call_kwargs = mock_webhook.call_args.kwargs
    assert call_kwargs.get("url") == "https://hooks.example.com/alert"
