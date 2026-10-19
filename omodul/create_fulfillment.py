"""omodul.create_fulfillment — 出库单据(履约追踪),防止超额履约。

本批次仓储模型的库存转换发生在 complete_checkout/mark_draft_order_paid
(预留→永久出库一次到位,见 complete_checkout 的 docstring),所以本函数
不碰 inventory_batch——它只追踪"这些订单行被打包进了这次发货",防止同一
行被超额履约(对比 order_line_item.quantity 与该订单所有非 canceled
fulfillment 的 items 数量之和)。

Composes:
  - obase.persistence.transaction

Pillars: decision_trail
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_TERMINAL_ORDER_STATUSES = {"canceled", "archived"}


class FulfillmentItem(BaseModel):
    order_line_item_id: str
    quantity: int


class CreateFulfillmentConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_fulfillment"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateFulfillmentInput(BaseModel):
    order_id: str
    items: list[FulfillmentItem]


async def create_fulfillment(
    config: CreateFulfillmentConfig,
    input_data: CreateFulfillmentInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """按订单行创建一份出库单据(可部分履约,支持多次调用打包不同行)。

    Args:
        config: CreateFulfillmentConfig。
        input_data: order_id / items(order_line_item_id + quantity)。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 fulfillment_id。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.items:
            raise ValueError("items must not be empty")
        if pool is None:
            raise ValueError("pool is required — create_fulfillment always touches persisted order")

        async with transaction(pool) as tx:
            order = await tx.fetchrow(
                'SELECT * FROM "customer_order" WHERE id = $1 FOR UPDATE', input_data.order_id
            )
            if order is None:
                raise ValueError(f"order {input_data.order_id} not found")
            if order["status"] in _TERMINAL_ORDER_STATUSES:
                raise ValueError(
                    f"order {input_data.order_id} is in terminal status {order['status']!r}"
                )

            existing_fulfillments = await tx.fetch(
                "SELECT items FROM \"fulfillment\" WHERE order_id = $1 AND status != 'canceled'",
                input_data.order_id,
            )
            already_fulfilled: dict[str, int] = {}
            for row in existing_fulfillments:
                for entry in json.loads(row["items"]):
                    key = entry["order_line_item_id"]
                    already_fulfilled[key] = already_fulfilled.get(key, 0) + entry["quantity"]

            for item in input_data.items:
                line = await tx.fetchrow(
                    'SELECT * FROM "order_line_item" WHERE id = $1 AND order_id = $2',
                    item.order_line_item_id,
                    input_data.order_id,
                )
                if line is None:
                    raise ValueError(
                        f"order_line_item {item.order_line_item_id} not found on order "
                        f"{input_data.order_id}"
                    )
                remaining = line["quantity"] - already_fulfilled.get(item.order_line_item_id, 0)
                if item.quantity > remaining:
                    raise ValueError(
                        f"order_line_item {item.order_line_item_id}: requested {item.quantity}, "
                        f"remaining unfulfilled {remaining}"
                    )

            fulfillment_id = uuid7()
            await tx.execute(
                'INSERT INTO "fulfillment" (id, order_id, items) VALUES ($1, $2, $3::jsonb)',
                fulfillment_id,
                input_data.order_id,
                json.dumps([item.model_dump() for item in input_data.items]),
            )
            trail.record(
                event="fulfillment_created",
                fulfillment_id=str(fulfillment_id),
                items=len(input_data.items),
            )

        if on_step:
            on_step(
                {
                    "stage": "create_fulfillment",
                    "status": "done",
                    "order_id": input_data.order_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            fulfillment_id=fulfillment_id,
            order_id=input_data.order_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
