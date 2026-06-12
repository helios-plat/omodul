"""Node register omodul."""

from __future__ import annotations

import hashlib
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from obase.docker import docker_node_info
from obase.persistence import PgPool, insert_one
from oprim import ssh_exec
from oskill import node_register_probe
from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class NodeRegisterConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "node_register"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"host", "node_label"}

    host: str
    node_label: str
    ssh_username: str
    db_dsn: str
    docker_connection_mode: Literal["auto", "tcp", "ssh"] = "auto"


class NodeRegisterInput(BaseModel):
    key_path: str | None = None
    password: str | None = None
    ssh_port: int = 22
    docker_tcp_port: int = 2375


class NodeRegisterFindings(BaseModel):
    node_id: str  # sha256(host + node_label)[:12]
    docker_host_url: str | None
    docker_mode: Literal["tcp", "ssh", "unreachable"]
    server_version: str | None
    os: str | None
    arch: str | None
    cpus: int | None
    memory_bytes: int | None
    registered_at_utc: str


async def node_register(
    config: NodeRegisterConfig,
    input_data: NodeRegisterInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """端到端注册节点: 探测 + 持久化 + 验证."""
    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    pool = await PgPool.get_or_create(dsn=config.db_dsn)
    token = _current_cost_tracker.set(cost_tracker)
    try:
        # _stage_probe
        probe_res = _stage_probe(config, input_data, trail_steps, on_step)

        # _stage_persist
        node_id = await _stage_persist(config, input_data, probe_res, pool, trail_steps, on_step)

        # _stage_verify
        _stage_verify(config, input_data, probe_res, trail_steps, on_step)

        findings = NodeRegisterFindings(
            node_id=node_id,
            docker_host_url=probe_res.docker_host_url,
            docker_mode=probe_res.docker_mode,
            server_version=probe_res.server_version,
            os=probe_res.os,
            arch=probe_res.arch,
            cpus=probe_res.cpus,
            memory_bytes=probe_res.memory_bytes,
            registered_at_utc=started_at.isoformat(),
        )

    except Exception as e:
        status = "failed"
        error_info = {
            "type": e.__class__.__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }
    finally:
        _current_cost_tracker.reset(token)

    output_dir.mkdir(parents=True, exist_ok=True)
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
        "status": status,
        "fingerprint": fingerprint,
        "findings": findings.model_dump() if findings else None,
        "error": error_info,
        "decision_trail": decision_trail,
        "report_path": str(report_path),
    }


def _stage_probe(
    config: NodeRegisterConfig,
    input_data: NodeRegisterInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
):
    step_start = datetime.now(UTC)
    res = node_register_probe(
        host=config.host,
        username=config.ssh_username,
        key_path=input_data.key_path,
        password=input_data.password,
        ssh_port=input_data.ssh_port,
        docker_tcp_port=input_data.docker_tcp_port,
    )
    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill",
        callable_name="node_register_probe",
        inputs_summary={"host": config.host},
        outputs_summary={"mode": res.docker_mode},
        started_at=step_start,
    )
    if res.docker_mode == "unreachable":
        raise Exception(f"Node unreachable or docker not detected: {res.error}")
    return res


async def _stage_persist(
    config: NodeRegisterConfig,
    input_data: NodeRegisterInput,
    probe_res: Any,
    pool: PgPool,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> str:
    step_start = datetime.now(UTC)
    node_id = hashlib.sha256(
        f"{config.host}:{config.node_label}".encode()
    ).hexdigest()[:12]
    await insert_one(
        pool,
        table="aegis_nodes",
        data={
            "node_id": node_id,
            "host": config.host,
            "node_label": config.node_label,
            "docker_host_url": probe_res.docker_host_url,
            "docker_mode": probe_res.docker_mode,
            "server_version": probe_res.server_version,
            "os": probe_res.os,
            "arch": probe_res.arch,
            "cpus": probe_res.cpus,
            "memory_bytes": probe_res.memory_bytes,
            "registered_at": datetime.now(UTC).isoformat(),
        },
        returning="node_id",
    )
    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="obase",
        callable_name="insert_one",
        inputs_summary={"table": "aegis_nodes", "node_id": node_id},
        outputs_summary={"result": "inserted"},
        started_at=step_start,
    )
    return node_id


def _stage_verify(
    config: NodeRegisterConfig,
    input_data: NodeRegisterInput,
    probe_res: Any,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
):
    step_start = datetime.now(UTC)
    v_name = "none"
    if probe_res.docker_mode == "tcp":
        docker_node_info(docker_host=probe_res.docker_host_url)
        v_name = "docker_node_info"
    elif probe_res.docker_mode == "ssh":
        ssh_exec(
            host=config.host,
            username=config.ssh_username,
            command="echo ok",
            port=input_data.ssh_port,
            key_path=input_data.key_path,
            password=input_data.password,
        )
        v_name = "ssh_exec"
    else:
        return

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="obase",
        callable_name=v_name,
        inputs_summary={"host": config.host},
        outputs_summary={"result": "verified"},
        started_at=step_start,
    )
