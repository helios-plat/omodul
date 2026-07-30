"""omodul.create_swap — 发起换货(退货 + 新发货 + 差价处理)。

new_items 立即预留库存(同 add_line_item_to_cart 的预留模式),return_items
只在 fulfill_swap 时才回补库存(旧货此时才算真正收回)——create_swap 阶段
只校验 return_items 属于该订单,不动它们的库存。price_difference_cents =
新品总价 - 退货总价:正数表示客户需补差价,负数表示客户应退差价,零表示
无需处理(payment_status 直接标 not_required,process_swap_payment 也就
不需要再调用)。

Composes:
  - obase.cache.DistributedLock(按各 batch_id 加锁,防止并发超卖 new_items)
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


class SwapReturnItem(BaseModel):
    order_line_item_id: str
    quantity: int


class SwapNewItem(BaseModel):
    batch_id: str
    quantity: int


class CreateSwapConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_swap"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateSwapInput(BaseModel):
    order_id: str
    return_items: list[SwapReturnItem]
    new_items: list[SwapNewItem]


async def create_swap(
    config: CreateSwapConfig,
    input_data: CreateSwapInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """发起换货申请,立即预留 new_items 库存,计算差价。

    Args:
        config: CreateSwapConfig。
        input_data: order_id / return_items / new_items。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 swap_id / price_difference_cents。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.return_items:
            raise ValueError("return_items must not be empty")
        if not input_data.new_items:
            raise ValueError("new_items must not be empty")
        if pool is None:
            raise ValueError("pool is required — create_swap always touches persisted order")

        async with transaction(pool) as tx:
            order = await tx.fetchrow(
                'SELECT * FROM "customer_order" WHERE id = $1', input_data.order_id
            )
            if order is None:
                raise ValueError(f"order {input_data.order_id} not found")
            if order["status"] in _TERMINAL_ORDER_STATUSES:
                raise ValueError(
                    f"order {input_data.order_id} is in terminal status {order['status']!r}"
                )

            return_total_cents = 0
            for item in input_data.return_items:
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
                if item.quantity > line["quantity"]:
                    raise ValueError(
                        f"order_line_item {item.order_line_item_id}: return quantity "
                        f"{item.quantity} exceeds ordered quantity {line['quantity']}"
                    )
                return_total_cents += line["unit_price_cents"] * item.quantity

            new_total_cents = 0
            for item in input_data.new_items:
                batch = await tx.fetchrow(
                    'SELECT * FROM "inventory_batch" WHERE id = $1 AND deleted_at IS NULL '
                    "FOR UPDATE",
                    item.batch_id,
                )
                if batch is None:
                    raise ValueError(f"batch {item.batch_id} not found")
                if batch["status"] != "active" or batch["inspection_status"] != "passed":
                    raise ValueError(f"batch {item.batch_id} is not sellable")
                available = batch["stock_qty"] - batch["reserved_qty"]
                if available < item.quantity:
                    raise ValueError(
                        f"insufficient stock for batch {item.batch_id}: requested "
                        f"{item.quantity}, available {available}"
                    )
                await tx.execute(
                    'UPDATE "inventory_batch" SET reserved_qty = reserved_qty + $1, '
                    "updated_at = NOW() WHERE id = $2",
                    item.quantity,
                    item.batch_id,
                )
                new_total_cents += batch["retail_price_cents"] * item.quantity
            trail.record(event="new_items_reserved", items=len(input_data.new_items))

            price_difference_cents = new_total_cents - return_total_cents
            payment_status = "not_required" if price_difference_cents == 0 else "not_paid"

            swap_id = uuid7()
            await tx.execute(
                'INSERT INTO "swap" '
                "(id, order_id, return_items, new_items, price_difference_cents, "
                "payment_status) VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6)",
                swap_id,
                input_data.order_id,
                json.dumps([item.model_dump() for item in input_data.return_items]),
                json.dumps([item.model_dump() for item in input_data.new_items]),
                price_difference_cents,
                payment_status,
            )
            trail.record(
                event="swap_created",
                swap_id=str(swap_id),
                price_difference_cents=price_difference_cents,
            )

        if on_step:
            on_step({"stage": "create_swap", "status": "done", "order_id": input_data.order_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            swap_id=swap_id,
            order_id=input_data.order_id,
            price_difference_cents=price_difference_cents,
            payment_status=payment_status,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
