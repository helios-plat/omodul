"""omodul.archive_order — 把订单归档(隐藏出活跃视图,不改库存/不改钱)。

纯状态位翻转,没有任何副作用——库存/退款联动都属于 cancel_order 的职责,
archive 只是"这单不用再关注了"的收尾标记,已归档的订单不能重复归档。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class ArchiveOrderConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "archive_order"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class ArchiveOrderInput(BaseModel):
    order_id: str


async def archive_order(
    config: ArchiveOrderConfig,
    input_data: ArchiveOrderInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """把订单标记为 archived。

    Args:
        config: ArchiveOrderConfig。
        input_data: order_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — archive_order always touches persisted order")

        order = await read_one(pool, table="customer_order", id=input_data.order_id)
        if order is None:
            raise ValueError(f"order {input_data.order_id} not found")
        if order["status"] == "archived":
            raise ValueError(f"order {input_data.order_id} is already archived")

        await update_one(
            pool, table="customer_order", id=input_data.order_id, data={"status": "archived"}
        )
        trail.record(event="order_archived", order_id=input_data.order_id)

        if on_step:
            on_step({"stage": "archive_order", "status": "done", "order_id": input_data.order_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            order_id=input_data.order_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
