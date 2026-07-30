"""omodul.update_stock_location — 局部更新仓位/门店。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_UPDATABLE_FIELDS = ("name", "region_code", "lat", "lng", "channel_tags", "status")


class UpdateStockLocationConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_stock_location"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UpdateStockLocationInput(BaseModel):
    location_id: str
    name: str | None = None
    region_code: str | None = None
    lat: float | None = None
    lng: float | None = None
    channel_tags: list[str] | None = None
    status: str | None = None


async def update_stock_location(
    config: UpdateStockLocationConfig,
    input_data: UpdateStockLocationInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新仓位/门店。

    Args:
        config: UpdateStockLocationConfig。
        input_data: location_id + 任意子集的可更新字段。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — update_stock_location always touches persisted location"
            )

        updates = {
            f: getattr(input_data, f)
            for f in _UPDATABLE_FIELDS
            if getattr(input_data, f) is not None
        }
        if not updates:
            raise ValueError("at least one field must be provided to update")

        location = await read_one(pool, table="stock_location", id=input_data.location_id)
        if location is None:
            raise ValueError(f"stock_location {input_data.location_id} not found")

        await update_one(pool, table="stock_location", id=input_data.location_id, data=updates)
        trail.record(
            event="location_updated",
            location_id=input_data.location_id,
            fields=list(updates.keys()),
        )

        if on_step:
            on_step(
                {
                    "stage": "update_stock_location",
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
            updated_fields=list(updates.keys()),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
