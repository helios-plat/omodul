"""omodul.remove_prices_from_list — 从价格表批量移除 SKU 特价(硬删)。

price_list_item 没有 deleted_at 列——纯池化条目,没有软删/审计需求,跟
product_collection_item 是同一类设计。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class RemovePricesFromListConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "remove_prices_from_list"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class RemovePricesFromListInput(BaseModel):
    price_list_id: str
    variant_ids: list[str]


async def remove_prices_from_list(
    config: RemovePricesFromListConfig,
    input_data: RemovePricesFromListInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """批量移除 SKU 特价。

    Args:
        config: RemovePricesFromListConfig。
        input_data: price_list_id / variant_ids。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 removed 计数。
    """
    from obase.persistence import transaction

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — remove_prices_from_list always touches persisted price_list"
            )
        if not input_data.variant_ids:
            raise ValueError("variant_ids must not be empty")

        async with transaction(pool) as tx:
            result = await tx.execute(
                'DELETE FROM "price_list_item" WHERE price_list_id = $1 AND variant_id = ANY($2)',
                input_data.price_list_id,
                input_data.variant_ids,
            )
            removed = int(result.split()[-1])
            trail.record(
                event="prices_removed", price_list_id=input_data.price_list_id, removed=removed
            )

        if on_step:
            on_step(
                {
                    "stage": "remove_prices_from_list",
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
            removed=removed,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
