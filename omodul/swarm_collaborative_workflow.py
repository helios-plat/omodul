"""omodul.swarm_collaborative_workflow — 群智协作事务.

集成：oskill.leader_worker_dispatch（任务分发）+ 注入的 worker runner
（每 worker 执行）+ 结果聚合报告。

Pillars: fingerprint, decision_trail, report, cost
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import (
    BaseConfig,
    CostTracker,
    Trail,
    build_result,
    compute_fingerprint,
    write_report,
)


class SwarmCollaborativeConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "swarm_collaborative_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"mission"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report", "cost"}

    strategy: str = "round_robin"
    report_format: str = "markdown"


class SwarmCollaborativeInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    mission: str
    tasks: list[dict] = []      # [{"id", "payload": dict}]
    workers: dict[str, Any] = {}  # {name: async (task_id, payload) -> Any}
    leader: Any = None          # 可选 leader（oskill LeaderHandle）


def compute_fingerprint_for(
    config: SwarmCollaborativeConfig, input_data: SwarmCollaborativeInput
) -> str:
    """Fingerprint over mission + task ids。"""
    return compute_fingerprint(
        {"mission": input_data.mission, "tasks": [t.get("id") for t in input_data.tasks]}
    )


async def swarm_collaborative_workflow(
    config: SwarmCollaborativeConfig,
    input_data: SwarmCollaborativeInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """群智协作：分发 → 并行执行 → 聚合。失败绝不 raise。"""
    trail = Trail()
    cost = CostTracker()
    fp = compute_fingerprint_for(config, input_data)
    t0 = time.time()

    try:
        if not input_data.mission.strip():
            raise ValueError("mission must not be empty")
        if not input_data.tasks:
            raise ValueError("tasks must not be empty")
        if not input_data.workers:
            raise ValueError("workers must not be empty")

        task_ids = [str(t.get("id", f"task{i}")) for i, t in enumerate(input_data.tasks)]
        payloads = {
            str(t.get("id", f"task{i}")): t.get("payload", {})
            for i, t in enumerate(input_data.tasks)
        }

        if on_step:
            on_step({"stage": "dispatch", "status": "running", "tasks": len(task_ids)})

        from oskill import leader_worker_dispatch

        outcome = await leader_worker_dispatch(
            task_ids,
            workers=input_data.workers,
            leader=input_data.leader,
            strategy=config.strategy,
            task_payloads=payloads,
        )
        trail.record(
            event="swarm_done", completed=outcome["completed"], failed=outcome["failed"],
        )

        report = _render_report(input_data, outcome, time.time() - t0)
        report_path = write_report(
            report, output_dir=Path(output_dir), name="swarm_collaborative",
            fmt=config.report_format,
        )
        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            report_path=report_path,
            cost_usd=round(cost.total_usd, 6),
            findings=outcome,
        )

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            cost_usd=round(cost.total_usd, 6),
        )


def _render_report(input_data, outcome: dict, elapsed: float) -> str:
    lines = [
        "# 群智协作报告",
        "",
        f"- 使命: {input_data.mission}",
        f"- 任务数: {outcome['completed'] + outcome['failed']} / 成功: {outcome['completed']}",
        f"- 耗时: {elapsed:.1f}s",
        "",
    ]
    for task, worker in outcome["assignments"].items():
        status = "✓" if task in outcome["results"] else "✗"
        lines.append(f"- {status} {task} → {worker}")
    if outcome["errors"]:
        lines.append("\n## 错误")
        for task, err in outcome["errors"].items():
            lines.append(f"- {task}: {err}")
    return "\n".join(lines)
