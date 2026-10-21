"""omodul.durable_lease_task_workflow — 持久租约任务事务.

集成：obase.durable_lease_queue（SQLite 租约队列）+ 注入的任务执行器：
入队 → 领取租约 → 执行 → complete/release（失败归还），崩溃安全。

Pillars: decision_trail, report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, write_report

Action = Literal["enqueue", "process"]


class DurableLeaseTaskConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "durable_lease_task_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "report"}

    lease_timeout_s: float = 60.0
    report_format: str = "markdown"


class DurableLeaseTaskInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    action: Action = "enqueue"
    queue: Any = None            # obase.durable_lease_queue.DurableLeaseQueue
    task_payload: dict = {}      # enqueue 模式
    priority: int = 0
    item_id: str | None = None   # enqueue 模式（可选）
    executor: Any = None         # process 模式：async (payload, item_id) -> Any
    worker_id: str = "worker-1"


async def durable_lease_task_workflow(
    config: DurableLeaseTaskConfig,
    input_data: DurableLeaseTaskInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """租约队列任务：enqueue 或 process（租约内执行）。失败绝不 raise。"""
    trail = Trail()

    try:
        if input_data.queue is None:
            raise ValueError("queue (DurableLeaseQueue) must be injected")

        if input_data.action == "enqueue":
            item_id = input_data.queue.enqueue(
                input_data.task_payload, priority=input_data.priority,
                item_id=input_data.item_id,
            )
            trail.record(event="enqueued", item_id=item_id)
            return build_result(
                status="completed",
                error=None,
                trail=trail,
                trail_path=trail.write(Path(output_dir)) if output_dir else None,
                item_id=item_id,
                action="enqueue",
            )

        if input_data.action == "process":
            if input_data.executor is None:
                raise ValueError("executor must be injected for process action")
            if on_step:
                on_step({"stage": "lease", "status": "running"})
            lease = input_data.queue.lease(
                input_data.worker_id, timeout_s=config.lease_timeout_s
            )
            if lease is None:
                trail.record(event="no_task", worker=input_data.worker_id)
                return build_result(
                    status="completed",
                    error=None,
                    trail=trail,
                    trail_path=trail.write(Path(output_dir)) if output_dir else None,
                    leased=False,
                    action="process",
                )

            trail.record(
                event="leased", item_id=lease["item_id"], lease_id=lease["lease_id"],
            )
            try:
                if on_step:
                    on_step({"stage": "execute", "item_id": lease["item_id"]})
                result = await input_data.executor(lease["payload"], lease["item_id"])
                done = input_data.queue.complete(lease["lease_id"])
                trail.record(
                    event="completed", item_id=lease["item_id"], done=done,
                )
                report_path = write_report(
                    f"# 租约任务报告\n\n- item: `{lease['item_id']}`\n"
                    f"- lease: `{lease['lease_id']}`\n- result: {result}\n",
                    output_dir=Path(output_dir), name="durable_lease_task",
                    fmt=config.report_format,
                )
                return build_result(
                    status="completed",
                    error=None,
                    trail=trail,
                    trail_path=trail.write(Path(output_dir)) if output_dir else None,
                    report_path=report_path,
                    leased=True,
                    item_id=lease["item_id"],
                    lease_id=lease["lease_id"],
                    result=result,
                )
            except Exception as exc:
                input_data.queue.release(lease["lease_id"])
                trail.record(
                    event="released_after_failure",
                    item_id=lease["item_id"], error=f"{type(exc).__name__}: {exc}",
                )
                return build_result(
                    status="failed",
                    error={"type": type(exc).__name__, "message": str(exc)},
                    trail=trail,
                    trail_path=trail.write(Path(output_dir)) if output_dir else None,
                    leased=True,
                    item_id=lease["item_id"],
                )

        raise ValueError(f"unknown action: {input_data.action}")

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
        )
