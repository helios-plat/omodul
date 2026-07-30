"""omodul.publish_products_to_channel — 批量上架商品到渠道。

已经上架过的商品(重复调用)直接跳过,不报错——上架是幂等操作,调用方
不需要先查一遍当前上架状态才敢调。

Composes:
  - obase.persistence.transaction

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class PublishProductsToChannelConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "publish_products_to_channel"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class PublishProductsToChannelInput(BaseModel):
    channel_id: str
    product_ids: list[str]


async def publish_products_to_channel(
    config: PublishProductsToChannelConfig,
    input_data: PublishProductsToChannelInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """批量上架商品到渠道(幂等,已上架的跳过)。

    Args:
        config: PublishProductsToChannelConfig。
        input_data: channel_id / product_ids。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 published(本次新上架数)。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — publish_products_to_channel always touches persisted channel"
            )
        if not input_data.product_ids:
            raise ValueError("product_ids must not be empty")

        published = 0

        async with transaction(pool) as tx:
            channel = await tx.fetchrow(
                'SELECT id FROM "sales_channel" WHERE id = $1 AND deleted_at IS NULL',
                input_data.channel_id,
            )
            if channel is None:
                raise ValueError(f"sales_channel {input_data.channel_id} not found")

            for product_id in input_data.product_ids:
                product = await tx.fetchrow(
                    'SELECT id FROM "product" WHERE id = $1 AND deleted_at IS NULL', product_id
                )
                if product is None:
                    raise ValueError(f"product {product_id} not found")

                existing = await tx.fetchrow(
                    'SELECT id FROM "sales_channel_product" WHERE channel_id = $1 '
                    "AND product_id = $2",
                    input_data.channel_id,
                    product_id,
                )
                if existing is not None:
                    continue

                await tx.execute(
                    'INSERT INTO "sales_channel_product" (id, channel_id, product_id) '
                    "VALUES ($1, $2, $3)",
                    uuid7(),
                    input_data.channel_id,
                    product_id,
                )
                published += 1

            trail.record(
                event="products_published", channel_id=input_data.channel_id, published=published
            )

        if on_step:
            on_step(
                {
                    "stage": "publish_products_to_channel",
                    "status": "done",
                    "channel_id": input_data.channel_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            channel_id=input_data.channel_id,
            published=published,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
