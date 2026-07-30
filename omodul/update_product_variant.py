"""omodul.update_product_variant — 局部更新 SKU。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class UpdateProductVariantConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_product_variant"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UpdateProductVariantInput(BaseModel):
    variant_id: str
    sku_code: str | None = None
    option_values: dict[str, str] | None = None
    reference_price_cents: int | None = None
    status: str | None = None


async def update_product_variant(
    config: UpdateProductVariantConfig,
    input_data: UpdateProductVariantInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新 SKU。

    Args:
        config: UpdateProductVariantConfig。
        input_data: variant_id + 任意子集的可更新字段。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    import json

    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — update_product_variant always touches persisted variant"
            )
        if input_data.reference_price_cents is not None and input_data.reference_price_cents < 0:
            raise ValueError("reference_price_cents must be non-negative")

        updates: dict[str, Any] = {}
        if input_data.sku_code is not None:
            updates["sku_code"] = input_data.sku_code
        if input_data.option_values is not None:
            updates["option_values"] = json.dumps(input_data.option_values)
        if input_data.reference_price_cents is not None:
            updates["reference_price_cents"] = input_data.reference_price_cents
        if input_data.status is not None:
            updates["status"] = input_data.status
        if not updates:
            raise ValueError("at least one field must be provided to update")

        variant = await read_one(pool, table="product_variant", id=input_data.variant_id)
        if variant is None:
            raise ValueError(f"product_variant {input_data.variant_id} not found")

        await update_one(pool, table="product_variant", id=input_data.variant_id, data=updates)
        trail.record(
            event="variant_updated",
            variant_id=input_data.variant_id,
            fields=list(updates.keys()),
        )

        if on_step:
            on_step(
                {
                    "stage": "update_product_variant",
                    "status": "done",
                    "variant_id": input_data.variant_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            variant_id=input_data.variant_id,
            updated_fields=list(updates.keys()),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
