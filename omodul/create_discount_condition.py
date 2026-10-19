"""omodul.create_discount_condition — 给折扣加一条 SKU/分类限制池条目。

一个 discount 下可以有多行 condition(池),apply_discount_to_cart 用
oskill.evaluate_discount_conditions 按这些行过滤适用的购物车行。

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

_VALID_CONDITION_TYPES = {"product", "category", "all"}


def compute_fingerprint_for(
    config: CreateDiscountConditionConfig, input_data: CreateDiscountConditionInput
) -> str:
    """Fingerprint over discount_id + condition_type + target_id。"""
    return compute_fingerprint(
        {
            "discount_id": input_data.discount_id,
            "condition_type": input_data.condition_type,
            "target_id": input_data.target_id,
        }
    )


class CreateDiscountConditionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_discount_condition"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"discount_id", "condition_type", "target_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class CreateDiscountConditionInput(BaseModel):
    discount_id: str
    condition_type: str  # 'product' | 'category' | 'all'
    target_id: str | None = None


async def create_discount_condition(
    config: CreateDiscountConditionConfig,
    input_data: CreateDiscountConditionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """加一条限制池条目。condition_type='all' 时 target_id 必须为空(整单折扣,
    不应该同时挂具体 SKU/分类,避免"限制池"语义自相矛盾)。

    Args:
        config: CreateDiscountConditionConfig。
        input_data: discount_id / condition_type / target_id(product/category 必填)。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 condition_id。
    """
    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if input_data.condition_type not in _VALID_CONDITION_TYPES:
            raise ValueError(f"invalid condition_type: {input_data.condition_type!r}")
        if input_data.condition_type in ("product", "category") and not input_data.target_id:
            raise ValueError(
                f"target_id is required for condition_type={input_data.condition_type!r}"
            )
        if input_data.condition_type == "all" and input_data.target_id:
            raise ValueError("target_id must be empty for condition_type='all'")

        fp = compute_fingerprint_for(config, input_data)

        condition_id = uuid7()
        row = {
            "id": condition_id,
            "discount_id": input_data.discount_id,
            "condition_type": input_data.condition_type,
            "target_id": input_data.target_id,
        }

        if pool is not None:
            await insert_one(pool, table="discount_condition", data=row)
            trail.record(event="persisted", condition_id=condition_id)
        else:
            trail.record(event="persisted_skipped_no_pool", condition_id=condition_id)

        if on_step:
            on_step(
                {
                    "stage": "create_discount_condition",
                    "status": "done",
                    "condition_id": condition_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            condition_id=condition_id,
            discount_id=input_data.discount_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
