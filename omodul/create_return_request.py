"""omodul.create_return_request — 发起退货申请(RMA 第一步:申请)。

只登记申请,不动库存/不退款(那是 receive_return 的职责——SPEC 原话
"入库加库存 → 算退款额 → 执行退款"三步合并在 receive_return 一个元素里)。
校验退货数量不超过该订单行"尚未申请退货"的剩余数量(跨多次 return_request
累计,同 create_fulfillment 的防超额履约校验思路)。

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


class ReturnItem(BaseModel):
    order_line_item_id: str
    quantity: int
    reason: str = ""


class CreateReturnRequestConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_return_request"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateReturnRequestInput(BaseModel):
    order_id: str
    items: list[ReturnItem]


async def create_return_request(
    config: CreateReturnRequestConfig,
    input_data: CreateReturnRequestInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """发起退货申请。

    Args:
        config: CreateReturnRequestConfig。
        input_data: order_id / items(order_line_item_id + quantity + reason)。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 return_request_id。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.items:
            raise ValueError("items must not be empty")
        if pool is None:
            raise ValueError(
                "pool is required — create_return_request always touches persisted order"
            )

        async with transaction(pool) as tx:
            order = await tx.fetchrow(
                'SELECT * FROM "customer_order" WHERE id = $1', input_data.order_id
            )
            if order is None:
                raise ValueError(f"order {input_data.order_id} not found")

            existing_requests = await tx.fetch(
                "SELECT items FROM \"return_request\" WHERE order_id = $1 AND status != 'canceled'",
                input_data.order_id,
            )
            already_requested: dict[str, int] = {}
            for row in existing_requests:
                for entry in json.loads(row["items"]):
                    key = entry["order_line_item_id"]
                    already_requested[key] = already_requested.get(key, 0) + entry["quantity"]

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
                remaining = line["quantity"] - already_requested.get(item.order_line_item_id, 0)
                if item.quantity > remaining:
                    raise ValueError(
                        f"order_line_item {item.order_line_item_id}: requested {item.quantity}, "
                        f"remaining returnable {remaining}"
                    )

            return_request_id = uuid7()
            await tx.execute(
                'INSERT INTO "return_request" (id, order_id, items) VALUES ($1, $2, $3::jsonb)',
                return_request_id,
                input_data.order_id,
                json.dumps([item.model_dump() for item in input_data.items]),
            )
            trail.record(
                event="return_request_created",
                return_request_id=str(return_request_id),
                items=len(input_data.items),
            )

        if on_step:
            on_step(
                {
                    "stage": "create_return_request",
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
            return_request_id=return_request_id,
            order_id=input_data.order_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
