"""Watch cycle omodul."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from obase.docker import docker_container_list
from obase.persistence import PgPool, insert_one
from oprim import http_post_webhook
from oskill import container_resource_rank, multi_node_health_sweep
from pydantic import BaseModel

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class WatchAlert(BaseModel):
    severity: Literal["critical", "warning", "info"]
    source: str  # "container:{name}" 或 "node:{host}"
    reason: str
    value: float | None = None


class WatchCycleConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "watch_cycle"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cycle_id"}

    cycle_id: str
    db_dsn: str
    alert_cpu_threshold: float = 85.0
    alert_mem_threshold: float = 90.0
    alert_container_down: bool = True


class WatchCycleInput(BaseModel):
    docker_hosts: list[str]
    webhook_url: str | None = None


class WatchCycleFindings(BaseModel):
    cycle_id: str
    scanned_nodes: int
    scanned_containers: int
    alerts_generated: list[WatchAlert]
    healthy_containers: int
    degraded_containers: int
    down_containers: int
    duration_ms: int


async def watch_cycle(
    config: WatchCycleConfig,
    input_data: WatchCycleInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """监控周期: 节点扫描 + 容器扫描 + 告警推送."""
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
        # 1. Node sweep
        node_res = _stage_node_sweep(config, input_data, trail_steps, on_step)

        # 2. Container sweep
        container_stats = _stage_container_sweep(
            config, input_data, node_res, trail_steps, on_step
        )

        # 3. Emit alerts
        alerts = node_res["alerts"] + container_stats["alerts"]
        await _stage_emit_alerts(config, input_data, alerts, pool, trail_steps, on_step)

        findings = WatchCycleFindings(
            cycle_id=config.cycle_id,
            scanned_nodes=len(input_data.docker_hosts),
            scanned_containers=container_stats["total"],
            alerts_generated=alerts,
            healthy_containers=container_stats["healthy"],
            degraded_containers=container_stats["degraded"],
            down_containers=container_stats["down"],
            duration_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1000),
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


def _stage_node_sweep(
    config: WatchCycleConfig,
    input_data: WatchCycleInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    step_start = datetime.now(UTC)
    res = multi_node_health_sweep(docker_hosts=input_data.docker_hosts)

    alerts = []
    for node in res.nodes:
        if not node.reachable:
            alerts.append(
                WatchAlert(
                    severity="critical",
                    source=f"node:{node.docker_host}",
                    reason="node unreachable",
                )
            )

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill",
        callable_name="multi_node_health_sweep",
        inputs_summary={"nodes_count": len(input_data.docker_hosts)},
        outputs_summary={"reachable": res.reachable_count, "alerts": len(alerts)},
        started_at=step_start,
    )
    return {"res": res, "alerts": alerts}


def _stage_container_sweep(
    config: WatchCycleConfig,
    input_data: WatchCycleInput,
    node_res: dict[str, Any],
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    step_start = datetime.now(UTC)
    total_scanned = 0
    healthy = 0
    degraded = 0
    down = 0
    alerts = []

    for node in node_res["res"].nodes:
        if not node.reachable:
            continue

        rank = container_resource_rank(docker_host=node.docker_host, top_n=100)
        for entry in rank.ranked:
            if entry.cpu_percent > config.alert_cpu_threshold:
                alerts.append(
                    WatchAlert(
                        severity="warning",
                        source=f"container:{entry.name}",
                        reason=f"high cpu usage: {entry.cpu_percent}%",
                        value=entry.cpu_percent,
                    )
                )
                degraded += 1
            elif entry.memory_percent > config.alert_mem_threshold:
                alerts.append(
                    WatchAlert(
                        severity="warning",
                        source=f"container:{entry.name}",
                        reason=f"high memory usage: {entry.memory_percent}%",
                        value=entry.memory_percent,
                    )
                )
                degraded += 1
            else:
                healthy += 1

        all_c = docker_container_list(all=True, docker_host=node.docker_host)
        total_scanned += len(all_c)
        for c in all_c:
            if c.state != "running":
                down += 1
                if config.alert_container_down:
                    alerts.append(
                        WatchAlert(
                            severity="critical",
                            source=f"container:{c.name}",
                            reason=f"container down (state: {c.state})",
                        )
                    )

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="oskill",
        callable_name="container_resource_rank",
        inputs_summary={"nodes": node_res["res"].reachable_count},
        outputs_summary={"total_containers": total_scanned, "alerts": len(alerts)},
        started_at=step_start,
    )
    return {
        "total": total_scanned,
        "healthy": healthy,
        "degraded": degraded,
        "down": down,
        "alerts": alerts,
    }


async def _stage_emit_alerts(
    config: WatchCycleConfig,
    input_data: WatchCycleInput,
    alerts: list[WatchAlert],
    pool: PgPool,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> None:
    step_start = datetime.now(UTC)
    for alert in alerts:
        if input_data.webhook_url:
            http_post_webhook(url=input_data.webhook_url, payload=alert.model_dump())

        await insert_one(
            pool,
            table="aegis_alert_events",
            data={
                "cycle_id": config.cycle_id,
                "severity": alert.severity,
                "source": alert.source,
                "reason": alert.reason,
                "value": alert.value,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    record_step(
        trail_steps=trail_steps,
        on_step=on_step,
        layer="obase",
        callable_name="insert_one",
        inputs_summary={"alerts_count": len(alerts)},
        outputs_summary={"status": "emitted"},
        started_at=step_start,
    )
