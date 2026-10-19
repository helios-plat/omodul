"""omodul.create_product_variant — 新建 SKU。

Composes:
  - obase.persistence.read_one(校验 product_id 存在)+ insert_one

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CreateProductVariantConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_product_variant"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateProductVariantInput(BaseModel):
    product_id: str
    sku_code: str
    option_values: dict[str, str] = {}
    reference_price_cents: int | None = None


async def create_product_variant(
    config: CreateProductVariantConfig,
    input_data: CreateProductVariantInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建 SKU。

    Args:
        config: CreateProductVariantConfig。
        input_data: product_id / sku_code / option_values / reference_price_cents。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 variant_id。
    """
    import json

    from obase.persistence import insert_one, read_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.sku_code:
            raise ValueError("sku_code is required")
        if input_data.reference_price_cents is not None and input_data.reference_price_cents < 0:
            raise ValueError("reference_price_cents must be non-negative")

        if pool is not None:
            product = await read_one(pool, table="product", id=input_data.product_id)
            if product is None:
                raise ValueError(f"product {input_data.product_id} not found")

        variant_id = uuid7()
        row = {
            "id": variant_id,
            "product_id": input_data.product_id,
            "sku_code": input_data.sku_code,
            "option_values": json.dumps(input_data.option_values),
            "reference_price_cents": input_data.reference_price_cents,
        }

        if pool is not None:
            await insert_one(pool, table="product_variant", data=row)
            trail.record(event="persisted", variant_id=variant_id)
        else:
            trail.record(event="persisted_skipped_no_pool", variant_id=variant_id)

        if on_step:
            on_step({"stage": "create_product_variant", "status": "done", "variant_id": variant_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            variant_id=variant_id,
            sku_code=input_data.sku_code,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
