"""omodul.cancel_batch_job — 取消一个长时任务(SPEC §4.10)。

只能从 created/running 取消,终态(completed/failed/canceled)拒绝重复取消。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_CANCELABLE_STATUSES = {"created", "running"}


class CancelBatchJobConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "cancel_batch_job"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CancelBatchJobInput(BaseModel):
    batch_job_id: str


async def cancel_batch_job(
    config: CancelBatchJobConfig,
    input_data: CancelBatchJobInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """取消一个长时任务。

    Args:
        config: CancelBatchJobConfig。
        input_data: batch_job_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import transaction

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — cancel_batch_job always touches persisted job")

        async with transaction(pool) as tx:
            job = await tx.fetchrow(
                'SELECT * FROM "batch_job" WHERE id = $1 FOR UPDATE', input_data.batch_job_id
            )
            if job is None:
                raise ValueError(f"batch_job {input_data.batch_job_id} not found")
            if job["status"] not in _CANCELABLE_STATUSES:
                raise ValueError(
                    f"batch_job {input_data.batch_job_id} cannot be canceled from status "
                    f"{job['status']!r}"
                )

            await tx.execute(
                "UPDATE \"batch_job\" SET status = 'canceled', updated_at = NOW() WHERE id = $1",
                input_data.batch_job_id,
            )
            trail.record(event="batch_job_canceled", batch_job_id=input_data.batch_job_id)

        if on_step:
            on_step(
                {
                    "stage": "cancel_batch_job",
                    "status": "done",
                    "batch_job_id": input_data.batch_job_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            batch_job_id=input_data.batch_job_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
