"""omodul.delete_stock_location — 软删仓位,前置校验无非删除批次引用。

跟 delete_region 同一套顾虑:有在架批次的仓位不允许删除(会留下指向"消失
仓位"的批次),先把批次转移/下架,或者干脆停用(status='inactive')。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class DeleteStockLocationConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_stock_location"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class DeleteStockLocationInput(BaseModel):
    location_id: str


async def delete_stock_location(
    config: DeleteStockLocationConfig,
    input_data: DeleteStockLocationInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删仓位;若存在任何引用该仓位的非删除批次则拒绝。

    Args:
        config: DeleteStockLocationConfig。
        input_data: location_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, soft_delete_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — delete_stock_location always touches persisted location"
            )

        location = await read_one(pool, table="stock_location", id=input_data.location_id)
        if location is None:
            raise ValueError(f"stock_location {input_data.location_id} not found")

        async with pool.acquire() as conn:
            batch_count = await conn.fetchval(
                'SELECT COUNT(*) FROM "inventory_batch" WHERE location_id = $1 '
                "AND deleted_at IS NULL",
                input_data.location_id,
            )
        if batch_count > 0:
            raise ValueError(
                f"stock_location {input_data.location_id} has {batch_count} batch(es) "
                "referencing it — cannot delete; use update_stock_location(status='inactive') "
                "instead"
            )

        deleted = await soft_delete_one(pool, table="stock_location", id=input_data.location_id)
        if not deleted:
            raise ValueError(
                f"stock_location {input_data.location_id} not found or already deleted"
            )
        trail.record(event="location_deleted", location_id=input_data.location_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_stock_location",
                    "status": "done",
                    "location_id": input_data.location_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            location_id=input_data.location_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
