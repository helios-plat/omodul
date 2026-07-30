"""omodul.delete_product_variant — 软删 SKU。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class DeleteProductVariantConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_product_variant"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class DeleteProductVariantInput(BaseModel):
    variant_id: str


async def delete_product_variant(
    config: DeleteProductVariantConfig,
    input_data: DeleteProductVariantInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删 SKU。

    Args:
        config: DeleteProductVariantConfig。
        input_data: variant_id。
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
                "pool is required — delete_product_variant always touches persisted variant"
            )

        deleted = await soft_delete_one(pool, table="product_variant", id=input_data.variant_id)
        if not deleted:
            raise ValueError(
                f"product_variant {input_data.variant_id} not found or already deleted"
            )
        trail.record(event="variant_deleted", variant_id=input_data.variant_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_product_variant",
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
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
