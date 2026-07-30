"""omodul.add_prices_to_list — 批量挂载/更新价格表下的 SKU 特价。

check-then-act 语义的 upsert:某个 SKU 在这张价格表里已经有价格,就更新;
没有就插入。不用数据库层 ON CONFLICT,跟本仓库其余元素的既有约定一致。

Composes:
  - obase.persistence.transaction

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class PriceListPriceItem(BaseModel):
    variant_id: str
    price_cents: int


class AddPricesToListConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "add_prices_to_list"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class AddPricesToListInput(BaseModel):
    price_list_id: str
    items: list[PriceListPriceItem]


async def add_prices_to_list(
    config: AddPricesToListConfig,
    input_data: AddPricesToListInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """批量挂载/更新 SKU 特价。

    Args:
        config: AddPricesToListConfig。
        input_data: price_list_id / items(每项 variant_id + price_cents)。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含每项的 created/updated 计数。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — add_prices_to_list always touches persisted price_list"
            )
        if not input_data.items:
            raise ValueError("items must not be empty")
        for item in input_data.items:
            if item.price_cents < 0:
                raise ValueError(f"price_cents must be non-negative for variant {item.variant_id}")

        created = 0
        updated = 0

        async with transaction(pool) as tx:
            price_list = await tx.fetchrow(
                'SELECT id FROM "price_list" WHERE id = $1 AND deleted_at IS NULL',
                input_data.price_list_id,
            )
            if price_list is None:
                raise ValueError(f"price_list {input_data.price_list_id} not found")

            for item in input_data.items:
                variant = await tx.fetchrow(
                    'SELECT id FROM "product_variant" WHERE id = $1 AND deleted_at IS NULL',
                    item.variant_id,
                )
                if variant is None:
                    raise ValueError(f"product_variant {item.variant_id} not found")

                existing = await tx.fetchrow(
                    'SELECT id FROM "price_list_item" WHERE price_list_id = $1 AND variant_id = $2',
                    input_data.price_list_id,
                    item.variant_id,
                )
                if existing is not None:
                    await tx.execute(
                        'UPDATE "price_list_item" SET price_cents = $1, updated_at = NOW() '
                        "WHERE id = $2",
                        item.price_cents,
                        existing["id"],
                    )
                    updated += 1
                else:
                    await tx.execute(
                        'INSERT INTO "price_list_item" '
                        "(id, price_list_id, variant_id, price_cents) "
                        "VALUES ($1, $2, $3, $4)",
                        uuid7(),
                        input_data.price_list_id,
                        item.variant_id,
                        item.price_cents,
                    )
                    created += 1

            trail.record(
                event="prices_added",
                price_list_id=input_data.price_list_id,
                created=created,
                updated=updated,
            )

        if on_step:
            on_step(
                {
                    "stage": "add_prices_to_list",
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
            created=created,
            updated=updated,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
