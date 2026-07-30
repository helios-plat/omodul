"""omodul.create_batch_job — 长时任务元数据登记(SPEC §4.10 系统批处理)。

只管生命周期记录,不含任何具体批处理业务逻辑(那是 oservi.bulk_import_worker/
bulk_export_worker 各自的编排职责,SPEC §5,尚未实现)——job_type/payload
都是调用方自定义的自由字段。

Pillars: decision_trail
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CreateBatchJobConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_batch_job"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateBatchJobInput(BaseModel):
    job_type: str
    payload: dict[str, Any] | None = None


async def create_batch_job(
    config: CreateBatchJobConfig,
    input_data: CreateBatchJobInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """登记一个长时任务。

    Args:
        config: CreateBatchJobConfig。
        input_data: job_type / payload(自定义结构)。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 batch_job_id。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.job_type:
            raise ValueError("job_type must not be empty")
        if pool is None:
            raise ValueError("pool is required — create_batch_job always touches persisted job")

        batch_job_id = uuid7()
        async with transaction(pool) as tx:
            await tx.execute(
                'INSERT INTO "batch_job" (id, job_type, payload) VALUES ($1, $2, $3::jsonb)',
                batch_job_id,
                input_data.job_type,
                json.dumps(input_data.payload) if input_data.payload is not None else None,
            )
        trail.record(event="batch_job_created", batch_job_id=str(batch_job_id))

        if on_step:
            on_step(
                {"stage": "create_batch_job", "status": "done", "batch_job_id": str(batch_job_id)}
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            batch_job_id=batch_job_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
