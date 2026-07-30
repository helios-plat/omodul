"""omodul.delete_discount — 软删折扣壳(不级联删 rule/condition,历史 cart_discount 记录保留)。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(config: DeleteDiscountConfig, input_data: DeleteDiscountInput) -> str:
    """Fingerprint over discount_id。"""
    return compute_fingerprint({"discount_id": input_data.discount_id})


class DeleteDiscountConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_discount"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"discount_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class DeleteDiscountInput(BaseModel):
    discount_id: str


async def delete_discount(
    config: DeleteDiscountConfig,
    input_data: DeleteDiscountInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删折扣。已经生效在某些购物车上的 cart_discount 行不受影响(历史分摊金额保留),
    但软删后的折扣无法再被 apply_discount_to_cart 查到(按 deleted_at IS NULL 过滤)。

    Args:
        config: DeleteDiscountConfig。
        input_data: discount_id。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import soft_delete_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — delete_discount always touches persisted discount")

        fp = compute_fingerprint_for(config, input_data)

        deleted = await soft_delete_one(pool, table="discount", id=input_data.discount_id)
        if not deleted:
            raise ValueError(f"discount {input_data.discount_id} not found or already deleted")
        trail.record(event="soft_deleted", discount_id=input_data.discount_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_discount",
                    "status": "done",
                    "discount_id": input_data.discount_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            discount_id=input_data.discount_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
