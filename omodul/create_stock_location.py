"""omodul.create_stock_location — 新建物理仓位/门店。

stock_location 表从批次仓储垂直一开始就建了(cart 域的 create_inventory_batch
依赖它),但一直没有专门的 omodul CRUD——本元素补上创建入口。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CreateStockLocationConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_stock_location"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateStockLocationInput(BaseModel):
    name: str
    region_code: str
    lat: float | None = None
    lng: float | None = None
    channel_tags: list[str] = []


async def create_stock_location(
    config: CreateStockLocationConfig,
    input_data: CreateStockLocationInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建物理仓位/门店。

    Args:
        config: CreateStockLocationConfig。
        input_data: name / region_code / lat / lng / channel_tags。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 location_id。
    """
    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.name:
            raise ValueError("name is required")
        if not input_data.region_code:
            raise ValueError("region_code is required")

        location_id = uuid7()
        row = {
            "id": location_id,
            "name": input_data.name,
            "region_code": input_data.region_code,
            "lat": input_data.lat,
            "lng": input_data.lng,
            "channel_tags": input_data.channel_tags,
        }

        if pool is not None:
            await insert_one(pool, table="stock_location", data=row)
            trail.record(event="persisted", location_id=location_id)
        else:
            trail.record(event="persisted_skipped_no_pool", location_id=location_id)

        if on_step:
            on_step(
                {"stage": "create_stock_location", "status": "done", "location_id": location_id}
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            location_id=location_id,
            name=input_data.name,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
