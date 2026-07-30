"""omodul.delete_discount_condition — 从限制池里移除一条条目。

discount_condition 没有 deleted_at 列(纯池化条目,没有"软删后仍可审计"的
需求),所以是硬删,不走 obase.persistence.soft_delete_one。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: DeleteDiscountConditionConfig, input_data: DeleteDiscountConditionInput
) -> str:
    """Fingerprint over condition_id。"""
    return compute_fingerprint({"condition_id": input_data.condition_id})


class DeleteDiscountConditionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_discount_condition"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"condition_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class DeleteDiscountConditionInput(BaseModel):
    condition_id: str


async def delete_discount_condition(
    config: DeleteDiscountConditionConfig,
    input_data: DeleteDiscountConditionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """从限制池硬删一条条目。

    Args:
        config: DeleteDiscountConditionConfig。
        input_data: condition_id。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — delete_discount_condition always touches persisted data"
            )

        fp = compute_fingerprint_for(config, input_data)

        async with pool.acquire() as conn:
            result = await conn.execute(
                'DELETE FROM "discount_condition" WHERE id = $1', input_data.condition_id
            )
        deleted = result.split()[-1] != "0"
        if not deleted:
            raise ValueError(f"discount_condition {input_data.condition_id} not found")
        trail.record(event="hard_deleted", condition_id=input_data.condition_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_discount_condition",
                    "status": "done",
                    "condition_id": input_data.condition_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            condition_id=input_data.condition_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
