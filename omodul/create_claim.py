"""omodul.create_claim — 发起客诉索赔(退款型或换新型)。

claim_type='refund' 时在创建时就算好 refund_amount_cents(items 的
unit_price_cents * quantity 之和),new_items 必须为空;claim_type='replace'
时立即预留 new_items 库存(同 create_swap 的预留模式),refund_amount_cents
留空。跟 create_return_request 一样做跨记录累计校验,防止同一订单行被
超额索赔(只在同订单未取消的 claim 之间累计,不跟 return_request/swap
互相校验——三者是各自独立的售后通道,SPEC 未要求互斥)。

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
_VALID_CLAIM_TYPES = {"refund", "replace"}


class ClaimItem(BaseModel):
    order_line_item_id: str
    quantity: int
    reason: str = ""


class ClaimNewItem(BaseModel):
    batch_id: str
    quantity: int


class CreateClaimConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_claim"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateClaimInput(BaseModel):
    order_id: str
    claim_type: str = "refund"
    items: list[ClaimItem]
    new_items: list[ClaimNewItem] | None = None


async def create_claim(
    config: CreateClaimConfig,
    input_data: CreateClaimInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """发起客诉索赔。

    Args:
        config: CreateClaimConfig。
        input_data: order_id / claim_type / items / new_items(仅 replace 需要)。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 claim_id。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if input_data.claim_type not in _VALID_CLAIM_TYPES:
            raise ValueError(f"claim_type must be one of {_VALID_CLAIM_TYPES}")
        if not input_data.items:
            raise ValueError("items must not be empty")
        if input_data.claim_type == "replace" and not input_data.new_items:
            raise ValueError("new_items is required for claim_type='replace'")
        if input_data.claim_type == "refund" and input_data.new_items:
            raise ValueError("new_items must not be set for claim_type='refund'")
        if pool is None:
            raise ValueError("pool is required — create_claim always touches persisted order")

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

            existing_claims = await tx.fetch(
                "SELECT items FROM \"claim\" WHERE order_id = $1 AND status != 'canceled'",
                input_data.order_id,
            )
            already_claimed: dict[str, int] = {}
            for row in existing_claims:
                for entry in json.loads(row["items"]):
                    key = entry["order_line_item_id"]
                    already_claimed[key] = already_claimed.get(key, 0) + entry["quantity"]

            refund_amount_cents = None
            if input_data.claim_type == "refund":
                refund_amount_cents = 0
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
                remaining = line["quantity"] - already_claimed.get(item.order_line_item_id, 0)
                if item.quantity > remaining:
                    raise ValueError(
                        f"order_line_item {item.order_line_item_id}: requested {item.quantity}, "
                        f"remaining claimable {remaining}"
                    )
                if input_data.claim_type == "refund":
                    refund_amount_cents += line["unit_price_cents"] * item.quantity

            if input_data.claim_type == "replace":
                for new_item in input_data.new_items:
                    batch = await tx.fetchrow(
                        'SELECT * FROM "inventory_batch" WHERE id = $1 AND deleted_at IS NULL '
                        "FOR UPDATE",
                        new_item.batch_id,
                    )
                    if batch is None:
                        raise ValueError(f"batch {new_item.batch_id} not found")
                    if batch["status"] != "active" or batch["inspection_status"] != "passed":
                        raise ValueError(f"batch {new_item.batch_id} is not sellable")
                    available = batch["stock_qty"] - batch["reserved_qty"]
                    if available < new_item.quantity:
                        raise ValueError(
                            f"insufficient stock for batch {new_item.batch_id}: requested "
                            f"{new_item.quantity}, available {available}"
                        )
                    await tx.execute(
                        'UPDATE "inventory_batch" SET reserved_qty = reserved_qty + $1, '
                        "updated_at = NOW() WHERE id = $2",
                        new_item.quantity,
                        new_item.batch_id,
                    )
                trail.record(event="new_items_reserved", items=len(input_data.new_items))

            claim_id = uuid7()
            await tx.execute(
                'INSERT INTO "claim" '
                "(id, order_id, claim_type, items, refund_amount_cents, new_items) "
                "VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb)",
                claim_id,
                input_data.order_id,
                input_data.claim_type,
                json.dumps([item.model_dump() for item in input_data.items]),
                refund_amount_cents,
                json.dumps([item.model_dump() for item in input_data.new_items])
                if input_data.new_items
                else None,
            )
            trail.record(event="claim_created", claim_id=str(claim_id))

        if on_step:
            on_step({"stage": "create_claim", "status": "done", "order_id": input_data.order_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            claim_id=claim_id,
            order_id=input_data.order_id,
            refund_amount_cents=refund_amount_cents,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
