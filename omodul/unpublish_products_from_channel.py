"""omodul.unpublish_products_from_channel — 批量下架商品。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class UnpublishProductsFromChannelConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "unpublish_products_from_channel"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UnpublishProductsFromChannelInput(BaseModel):
    channel_id: str
    product_ids: list[str]


async def unpublish_products_from_channel(
    config: UnpublishProductsFromChannelConfig,
    input_data: UnpublishProductsFromChannelInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """批量下架商品。

    Args:
        config: UnpublishProductsFromChannelConfig。
        input_data: channel_id / product_ids。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 unpublished(实际移除数)。
    """
    from obase.persistence import transaction

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — unpublish_products_from_channel always touches "
                "persisted channel"
            )
        if not input_data.product_ids:
            raise ValueError("product_ids must not be empty")

        async with transaction(pool) as tx:
            result = await tx.execute(
                'DELETE FROM "sales_channel_product" WHERE channel_id = $1 '
                "AND product_id = ANY($2)",
                input_data.channel_id,
                input_data.product_ids,
            )
            unpublished = int(result.split()[-1])
            trail.record(
                event="products_unpublished",
                channel_id=input_data.channel_id,
                unpublished=unpublished,
            )

        if on_step:
            on_step(
                {
                    "stage": "unpublish_products_from_channel",
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
            unpublished=unpublished,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
