"""omodul.delete_region — 软删区域,前置校验无订单引用。

有订单历史的区域不允许删除(哪怕订单已经 canceled/archived——历史记录里
的 region_code 不该指向一个消失的区域),要停用一个区域用
update_region(status='inactive'),不是删除。

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(config: DeleteRegionConfig, input_data: DeleteRegionInput) -> str:
    """Fingerprint over code。"""
    return compute_fingerprint({"code": input_data.code})


class DeleteRegionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_region"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"code"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class DeleteRegionInput(BaseModel):
    code: str


async def delete_region(
    config: DeleteRegionConfig,
    input_data: DeleteRegionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删区域;若存在任何引用该 region_code 的订单则拒绝。

    Args:
        config: DeleteRegionConfig。
        input_data: code。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, soft_delete_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — delete_region always touches persisted region")

        fp = compute_fingerprint_for(config, input_data)

        region = await read_one(pool, table="region", id=input_data.code, id_column="code")
        if region is None:
            raise ValueError(f"region {input_data.code!r} not found")

        async with pool.acquire() as conn:
            order_count = await conn.fetchval(
                'SELECT COUNT(*) FROM "customer_order" WHERE region_code = $1', input_data.code
            )
        if order_count > 0:
            raise ValueError(
                f"region {input_data.code!r} has {order_count} order(s) referencing it "
                "— cannot delete; use update_region(status='inactive') instead"
            )

        deleted = await soft_delete_one(pool, table="region", id=input_data.code, id_column="code")
        if not deleted:
            raise ValueError(f"region {input_data.code!r} not found or already deleted")
        trail.record(event="region_deleted", code=input_data.code)

        if on_step:
            on_step({"stage": "delete_region", "status": "done", "code": input_data.code})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            code=input_data.code,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
