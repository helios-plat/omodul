"""omodul.update_price_list — 局部更新价格表元数据。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_UPDATABLE_FIELDS = ("name", "currency", "starts_at", "ends_at", "status")
_DATETIME_FIELDS = {"starts_at", "ends_at"}


class UpdatePriceListConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_price_list"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UpdatePriceListInput(BaseModel):
    price_list_id: str
    name: str | None = None
    currency: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    status: str | None = None


async def update_price_list(
    config: UpdatePriceListConfig,
    input_data: UpdatePriceListInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新价格表元数据。

    Args:
        config: UpdatePriceListConfig。
        input_data: price_list_id + 任意子集的可更新字段。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from datetime import datetime

    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — update_price_list always touches persisted price_list"
            )

        updates: dict[str, Any] = {}
        for f in _UPDATABLE_FIELDS:
            value = getattr(input_data, f)
            if value is None:
                continue
            updates[f] = datetime.fromisoformat(value) if f in _DATETIME_FIELDS else value
        if not updates:
            raise ValueError("at least one field must be provided to update")

        price_list = await read_one(pool, table="price_list", id=input_data.price_list_id)
        if price_list is None:
            raise ValueError(f"price_list {input_data.price_list_id} not found")

        await update_one(pool, table="price_list", id=input_data.price_list_id, data=updates)
        trail.record(
            event="price_list_updated",
            price_list_id=input_data.price_list_id,
            fields=list(updates.keys()),
        )

        if on_step:
            on_step(
                {
                    "stage": "update_price_list",
                    "status": "done",
                    "price_list_id": input_data.price_list_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            price_list_id=input_data.price_list_id,
            updated_fields=list(updates.keys()),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
