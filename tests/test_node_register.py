"""Tests for node_register omodul (batch-3 obase.persistence + obase.docker migration)."""

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

from omodul.node_register import (  # noqa: E402
    NodeRegisterConfig,
    NodeRegisterInput,
    node_register,
)


def _make_config(tmp_path: Path) -> NodeRegisterConfig:
    return NodeRegisterConfig(
        host="192.168.1.10",
        node_label="worker-01",
        ssh_username="ubuntu",
        db_dsn="postgresql://user:pass@localhost/aegis",
    )


def _make_input() -> NodeRegisterInput:
    return NodeRegisterInput(ssh_port=22, docker_tcp_port=2375)


def _make_probe_result(mode: str = "tcp") -> MagicMock:
    r = MagicMock()
    r.docker_mode = mode
    r.docker_host_url = "tcp://192.168.1.10:2375"
    r.server_version = "24.0.1"
    r.os = "linux"
    r.arch = "amd64"
    r.cpus = 4
    r.memory_bytes = 8589934592
    r.error = None
    return r


@pytest.mark.asyncio
@patch("omodul.node_register.docker_node_info")
@patch("omodul.node_register.insert_one", new_callable=AsyncMock)
@patch("omodul.node_register.node_register_probe")
@patch("omodul.node_register.PgPool.get_or_create", new_callable=AsyncMock)
async def test_node_register_completed_status(
    mock_get_or_create, mock_probe, mock_insert, mock_docker_info, tmp_path
):
    mock_get_or_create.return_value = MagicMock()
    mock_probe.return_value = _make_probe_result("tcp")
    mock_insert.return_value = "abc123def456"

    config = _make_config(tmp_path)
    res = await node_register(config, _make_input(), tmp_path / "out")

    assert res["status"] == "completed"
    assert res["fingerprint"] is not None


@pytest.mark.asyncio
@patch("omodul.node_register.docker_node_info")
@patch("omodul.node_register.insert_one", new_callable=AsyncMock)
@patch("omodul.node_register.node_register_probe")
@patch("omodul.node_register.PgPool.get_or_create", new_callable=AsyncMock)
async def test_node_register_findings_populated(
    mock_get_or_create, mock_probe, mock_insert, mock_docker_info, tmp_path
):
    mock_get_or_create.return_value = MagicMock()
    probe = _make_probe_result("tcp")
    mock_probe.return_value = probe
    mock_insert.return_value = "abc123def456"

    config = _make_config(tmp_path)
    res = await node_register(config, _make_input(), tmp_path / "out")

    assert res["findings"] is not None
    f = res["findings"]
    assert len(f["node_id"]) == 12
    assert f["docker_mode"] == "tcp"
    assert f["os"] == "linux"
    assert f["cpus"] == 4


@pytest.mark.asyncio
@patch("omodul.node_register.PgPool.get_or_create", new_callable=AsyncMock)
@patch("omodul.node_register.node_register_probe")
async def test_node_register_probe_unreachable_fails(
    mock_probe, mock_get_or_create, tmp_path
):
    mock_get_or_create.return_value = MagicMock()
    probe = _make_probe_result("unreachable")
    probe.error = "connection refused"
    mock_probe.return_value = probe

    config = _make_config(tmp_path)
    res = await node_register(config, _make_input(), tmp_path / "out")

    assert res["status"] == "failed"
    assert res["findings"] is None


@pytest.mark.asyncio
@patch("omodul.node_register.docker_node_info")
@patch("omodul.node_register.insert_one", new_callable=AsyncMock)
@patch("omodul.node_register.node_register_probe")
@patch("omodul.node_register.PgPool.get_or_create", new_callable=AsyncMock)
async def test_node_register_insert_called_with_aegis_nodes(
    mock_get_or_create, mock_probe, mock_insert, mock_docker_info, tmp_path
):
    mock_pool = MagicMock()
    mock_get_or_create.return_value = mock_pool
    mock_probe.return_value = _make_probe_result("tcp")
    mock_insert.return_value = "abc123def456"

    config = _make_config(tmp_path)
    await node_register(config, _make_input(), tmp_path / "out")

    mock_insert.assert_called_once()
    call_kwargs = mock_insert.call_args
    assert call_kwargs.kwargs.get("table") == "aegis_nodes" or (
        len(call_kwargs.args) > 1 and False
    )
    assert "aegis_nodes" in str(call_kwargs)


@pytest.mark.asyncio
@patch("omodul.node_register.docker_node_info")
@patch("omodul.node_register.insert_one", new_callable=AsyncMock)
@patch("omodul.node_register.node_register_probe")
@patch("omodul.node_register.PgPool.get_or_create", new_callable=AsyncMock)
async def test_node_register_decision_trail_written(
    mock_get_or_create, mock_probe, mock_insert, mock_docker_info, tmp_path
):
    mock_get_or_create.return_value = MagicMock()
    mock_probe.return_value = _make_probe_result("tcp")
    mock_insert.return_value = "abc123def456"

    config = _make_config(tmp_path)
    res = await node_register(config, _make_input(), tmp_path / "out")

    trail = res["decision_trail"]
    assert trail["omodul_name"] == "node_register"
    assert len(trail["steps"]) >= 2
