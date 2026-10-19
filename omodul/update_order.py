"""omodul.update_order — 订单状态/收货地址的通用局部更新。

只做字段写入,不含业务规则(取消/归档各有专门的元素,承载各自的副作用)。
终态订单(canceled/archived)拒绝任何更新——要恢复应该走新单据,不是改老单。

Pillars: decision_trail(SPEC 未给 fingerprint,遵照原样)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_TERMINAL_STATUSES = {"canceled", "archived"}


class UpdateOrderConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_order"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UpdateOrderInput(BaseModel):
    order_id: str
    status: str | None = None
    shipping_address: dict[str, Any] | None = None


async def update_order(
    config: UpdateOrderConfig,
    input_data: UpdateOrderInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新订单(status/shipping_address),终态订单拒绝更新。

    Args:
        config: UpdateOrderConfig。
        input_data: order_id + 任意子集的可更新字段。
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
            raise ValueError("pool is required — update_order always touches persisted order")

        updates: dict[str, Any] = {}
        if input_data.status is not None:
            updates["status"] = input_data.status
        if input_data.shipping_address is not None:
            updates["shipping_address"] = json.dumps(input_data.shipping_address)
        if not updates:
            raise ValueError("at least one field must be provided to update")

        order = await read_one(pool, table="customer_order", id=input_data.order_id)
        if order is None:
            raise ValueError(f"order {input_data.order_id} not found")
        if order["status"] in _TERMINAL_STATUSES:
            raise ValueError(
                f"order {input_data.order_id} is in terminal status {order['status']!r} "
                "and cannot be updated"
            )

        await update_one(pool, table="customer_order", id=input_data.order_id, data=updates)
        trail.record(
            event="order_updated", order_id=input_data.order_id, fields=list(updates.keys())
        )

        if on_step:
            on_step({"stage": "update_order", "status": "done", "order_id": input_data.order_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            order_id=input_data.order_id,
            updated_fields=list(updates.keys()),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
