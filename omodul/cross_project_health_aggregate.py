from __future__ import annotations

import concurrent.futures
import contextvars
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from oprim import docker_container_list, http_health_probe

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint


class CrossProjectHealthConfig(BaseConfig):
    """跨项目健康度聚合配置."""

    _omodul_name: ClassVar[str] = "cross_project_health_aggregate"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"label_key", "time_window_seconds"}

    label_key: str = "aegis.project"
    time_window_seconds: int = 30
    parallel_workers: int = 8


def compute_fingerprint_for(config: CrossProjectHealthConfig, input_data: None) -> str:
    """计算聚合指纹."""
    return compute_fingerprint(config, input_data)


def cross_project_health_aggregate(
    config: CrossProjectHealthConfig,
    input_data: None,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """跨项目健康度聚合.

    主流程:
    1. _stage_discover (调 docker 列 containers with label, group by label_key)
    2. _stage_fetch_health (ThreadPool + contextvars.copy_context 并发调各 /health)
    3. _stage_aggregate (ok / degraded / down)
    4. _stage_report (markdown 项目矩阵表 + 异常列表)

    Args:
        config: 聚合配置
        input_data: 无
        output_dir: 结果目录
        on_step: 回调

    Returns:
        结果字典
    """
    started_at = datetime.now(UTC)
    cost_tracker = CostTracker()
    trail_steps: list[dict[str, Any]] = []
    status: Literal["completed", "failed", "partial"] = "completed"
    error: dict[str, Any] | None = None
    findings: dict[str, Any] = {}

    fingerprint = compute_fingerprint_for(config, input_data)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.md"

    try:
        # 1. _stage_discover
        t0 = datetime.now(UTC)
        containers = docker_container_list(filters={"label": [config.label_key]})

        projects: dict[str, list[Any]] = {}
        for c in containers:
            proj_name = c.labels.get(config.label_key, "unknown")
            if proj_name not in projects:
                projects[proj_name] = []
            projects[proj_name].append(c)

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="oprim",
            callable_name="docker_container_list",
            inputs_summary={"label_key": config.label_key},
            outputs_summary={"projects_count": len(projects), "containers_count": len(containers)},
            started_at=t0,
        )

        # 2. _stage_fetch_health
        t0 = datetime.now(UTC)
        health_results: dict[str, dict[str, Any]] = {}

        # Use ThreadPool for parallel health checks
        ctx = contextvars.copy_context()

        def check_health(container: Any) -> tuple[str, str, dict[str, Any]]:
            # Logic to find health URL (simplified for this modul)
            try:
                # In real scenario, would map container ports to host or use network
                # Here we use a hypothetical URL for probe
                probe_res = http_health_probe(url=f"http://{container.name}:8080/health")
                return (
                    container.container_id,
                    "ok" if probe_res.healthy else "down",
                    probe_res.model_dump(),
                )
            except Exception as e:
                return (container.container_id, "down", {"error": str(e)})

        with concurrent.futures.ThreadPoolExecutor(max_workers=config.parallel_workers) as executor:
            future_to_c = {executor.submit(ctx.run, check_health, c): c for c in containers}
            for future in concurrent.futures.as_completed(future_to_c):
                c_id, h_status, h_detail = future.result()
                health_results[c_id] = {"status": h_status, "detail": h_detail}

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="external",
            callable_name="_stage_fetch_health",
            inputs_summary={"workers": config.parallel_workers},
            outputs_summary={"results_count": len(health_results)},
            started_at=t0,
        )

        # 3. _stage_aggregate
        t0 = datetime.now(UTC)
        project_agg = {}
        for proj_name, proj_containers in projects.items():
            statuses = [health_results[c.container_id]["status"] for c in proj_containers]
            if all(s == "ok" for s in statuses):
                agg_status = "ok"
            elif any(s == "ok" for s in statuses):
                agg_status = "degraded"
            else:
                agg_status = "down"
            project_agg[proj_name] = {
                "status": agg_status,
                "total": len(statuses),
                "ok": statuses.count("ok"),
                "down": statuses.count("down"),
            }

        findings["project_aggregation"] = project_agg

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="external",
            callable_name="_stage_aggregate",
            inputs_summary={},
            outputs_summary={"aggregated_projects": len(project_agg)},
            started_at=t0,
        )

        # 4. _stage_report
        report_path = output_dir / "report.md"
        with open(report_path, "w") as f:
            f.write("# Cross Project Health Aggregation\n\n")
            f.write(f"Generated at: {datetime.now(UTC).isoformat()}\n\n")
            f.write("| Project | Status | OK/Total |\n")
            f.write("| --- | --- | --- |\n")
            for proj, data in project_agg.items():
                f.write(f"| {proj} | {data['status']} | {data['ok']}/{data['total']} |\n")

        record_step(
            trail_steps=trail_steps,
            on_step=on_step,
            layer="external",
            callable_name="_stage_report",
            inputs_summary={"report_path": str(report_path)},
            outputs_summary={"status": "written"},
            started_at=datetime.now(UTC),
        )

    except Exception as e:
        status = "failed"
        error = {"type": type(e).__name__, "message": str(e)}

    decision_trail = build_decision_trail(
        fingerprint=fingerprint,
        config=config,
        input_data=input_data,
        trail_steps=trail_steps,
        cost_tracker=cost_tracker,
        started_at=started_at,
        status=status,
        error=error,
    )

    with open(output_dir / "decision_trail.json", "w") as f:
        json.dump(decision_trail, f, indent=2)

    return {
        "findings": findings,
        "fingerprint": fingerprint,
        "decision_trail": decision_trail,
        "report_path": str(report_path),
        "cost_usd": cost_tracker.total_usd,
        "status": status,
        "error": error,
    }
