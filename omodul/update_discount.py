"""omodul.update_discount — 更新折扣壳状态(active/inactive)。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(config: UpdateDiscountConfig, input_data: UpdateDiscountInput) -> str:
    """Fingerprint over discount_id + status。"""
    return compute_fingerprint({"discount_id": input_data.discount_id, "status": input_data.status})


class UpdateDiscountConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_discount"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"discount_id", "status"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class UpdateDiscountInput(BaseModel):
    discount_id: str
    status: str  # 'active' | 'inactive'


async def update_discount(
    config: UpdateDiscountConfig,
    input_data: UpdateDiscountInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """切换折扣状态。inactive 的折扣在 apply_discount_to_cart 里会被 eligibility 判定挡住
    (前提是调用方把 status 一并纳入 evaluate_discount_eligibility 的 rule —— 本函数只负责
    落库,资格判定逻辑在 apply_discount_to_cart)。

    Args:
        config: UpdateDiscountConfig。
        input_data: discount_id / status。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if input_data.status not in ("active", "inactive"):
            raise ValueError(f"invalid status: {input_data.status!r}")
        if pool is None:
            raise ValueError("pool is required — update_discount always touches persisted discount")

        fp = compute_fingerprint_for(config, input_data)

        discount = await read_one(pool, table="discount", id=input_data.discount_id)
        if discount is None:
            raise ValueError(f"discount {input_data.discount_id} not found")

        await update_one(
            pool, table="discount", id=input_data.discount_id, data={"status": input_data.status}
        )
        trail.record(
            event="status_updated", discount_id=input_data.discount_id, status=input_data.status
        )

        if on_step:
            on_step(
                {
                    "stage": "update_discount",
                    "status": "done",
                    "discount_id": input_data.discount_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            discount_id=input_data.discount_id,
            status_value=input_data.status,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
