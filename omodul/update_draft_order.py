"""omodul.update_draft_order — 改草稿订单的客户/地址(不改行项目)。

行项目变更没有增量接口(见 create_draft_order 的范围声明)——要改数量/
商品,删了用 delete_draft_order 重建。只能对 status='draft' 的订单生效。

Pillars: decision_trail
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class UpdateDraftOrderConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_draft_order"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UpdateDraftOrderInput(BaseModel):
    order_id: str
    customer_id: str | None = None
    billing_address: dict[str, Any] | None = None
    shipping_address: dict[str, Any] | None = None


async def update_draft_order(
    config: UpdateDraftOrderConfig,
    input_data: UpdateDraftOrderInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新草稿订单的客户/地址字段。

    Args:
        config: UpdateDraftOrderConfig。
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
            raise ValueError("pool is required — update_draft_order always touches persisted order")

        updates: dict[str, Any] = {}
        if input_data.customer_id is not None:
            updates["customer_id"] = input_data.customer_id
        if input_data.billing_address is not None:
            updates["billing_address"] = json.dumps(input_data.billing_address)
        if input_data.shipping_address is not None:
            updates["shipping_address"] = json.dumps(input_data.shipping_address)
        if not updates:
            raise ValueError("at least one field must be provided to update")

        order = await read_one(pool, table="customer_order", id=input_data.order_id)
        if order is None:
            raise ValueError(f"order {input_data.order_id} not found")
        if order["status"] != "draft":
            raise ValueError(
                f"order {input_data.order_id} is not a draft (status={order['status']!r})"
            )

        await update_one(pool, table="customer_order", id=input_data.order_id, data=updates)
        trail.record(
            event="draft_order_updated", order_id=input_data.order_id, fields=list(updates.keys())
        )

        if on_step:
            on_step(
                {"stage": "update_draft_order", "status": "done", "order_id": input_data.order_id}
            )

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
