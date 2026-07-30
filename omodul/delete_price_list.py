"""omodul.delete_price_list — 软删价格表(不清理 price_list_item,同
product_collection 先例)。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class DeletePriceListConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_price_list"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class DeletePriceListInput(BaseModel):
    price_list_id: str


async def delete_price_list(
    config: DeletePriceListConfig,
    input_data: DeletePriceListInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删价格表。

    Args:
        config: DeletePriceListConfig。
        input_data: price_list_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import soft_delete_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — delete_price_list always touches persisted price_list"
            )

        deleted = await soft_delete_one(pool, table="price_list", id=input_data.price_list_id)
        if not deleted:
            raise ValueError(f"price_list {input_data.price_list_id} not found or already deleted")
        trail.record(event="price_list_deleted", price_list_id=input_data.price_list_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_price_list",
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
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
