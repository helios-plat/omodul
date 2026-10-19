"""omodul.fulfill_claim — 执行客诉索赔的处理结果。

claim_type='refund':调用 ext_pay_refund 退款,累加 order.refunded_cents
(拒绝超过 grand_total_cents)。claim_type='replace':new_items 预留转永久
出库(同 fulfill_swap 的转换模式),建履约单据。

Composes:
  - obase.persistence.transaction
  - oprim.ext_pay_refund

Pillars: decision_trail, cost
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class FulfillClaimConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "fulfill_claim"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}


class FulfillClaimInput(BaseModel):
    claim_id: str


async def fulfill_claim(
    config: FulfillClaimConfig,
    input_data: FulfillClaimInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """执行客诉索赔:refund 型退款,replace 型转正出库。

    Args:
        config: FulfillClaimConfig。
        input_data: claim_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 视 claim_type 含 refund_result 或
        fulfillment_id。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7
    from oprim.ext_pay_refund import ext_pay_refund

    trail = Trail()
    refund_result = None
    fulfillment_id = None

    try:
        if pool is None:
            raise ValueError("pool is required — fulfill_claim always touches persisted claim")

        async with transaction(pool) as tx:
            claim = await tx.fetchrow(
                'SELECT * FROM "claim" WHERE id = $1 FOR UPDATE', input_data.claim_id
            )
            if claim is None:
                raise ValueError(f"claim {input_data.claim_id} not found")
            if claim["status"] != "pending":
                raise ValueError(
                    f"claim {input_data.claim_id} cannot be fulfilled from status "
                    f"{claim['status']!r}"
                )

            if claim["claim_type"] == "refund":
                order = await tx.fetchrow(
                    'SELECT * FROM "customer_order" WHERE id = $1 FOR UPDATE', claim["order_id"]
                )
                if order is None:
                    raise ValueError(f"order {claim['order_id']} not found")
                if not order["payment_provider_name"] or not order["payment_intent_id"]:
                    raise ValueError(f"order {claim['order_id']} has no payment intent to refund")

                refund_amount = claim["refund_amount_cents"]
                already_refunded = order["refunded_cents"] or 0
                if already_refunded + refund_amount > order["grand_total_cents"]:
                    raise ValueError(
                        f"refund {refund_amount} would exceed grand_total: already refunded "
                        f"{already_refunded}, grand_total {order['grand_total_cents']}"
                    )

                refund_result = await ext_pay_refund(
                    order["payment_provider_name"],
                    intent_id=order["payment_intent_id"],
                    amount=refund_amount,
                )
                await tx.execute(
                    'UPDATE "customer_order" SET refunded_cents = refunded_cents + $1, '
                    "updated_at = NOW() WHERE id = $2",
                    refund_amount,
                    claim["order_id"],
                )
                trail.record(
                    event="claim_refunded", claim_id=input_data.claim_id, amount_cents=refund_amount
                )
            else:
                new_items = json.loads(claim["new_items"])
                for item in new_items:
                    await tx.execute(
                        'UPDATE "inventory_batch" SET stock_qty = stock_qty - $1, '
                        "reserved_qty = reserved_qty - $1, updated_at = NOW() WHERE id = $2",
                        item["quantity"],
                        item["batch_id"],
                    )
                trail.record(event="new_items_converted_to_sale", items=len(new_items))

                fulfillment_id = uuid7()
                await tx.execute(
                    'INSERT INTO "fulfillment" (id, order_id, items) VALUES ($1, $2, $3::jsonb)',
                    fulfillment_id,
                    claim["order_id"],
                    json.dumps(new_items),
                )
                await tx.execute(
                    'UPDATE "claim" SET fulfillment_id = $1, updated_at = NOW() WHERE id = $2',
                    fulfillment_id,
                    input_data.claim_id,
                )
                trail.record(
                    event="claim_replacement_shipped",
                    claim_id=input_data.claim_id,
                    fulfillment_id=str(fulfillment_id),
                )

            await tx.execute(
                "UPDATE \"claim\" SET status = 'fulfilled', updated_at = NOW() WHERE id = $1",
                input_data.claim_id,
            )

        if on_step:
            on_step({"stage": "fulfill_claim", "status": "done", "claim_id": input_data.claim_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            claim_id=input_data.claim_id,
            refund_result=refund_result,
            fulfillment_id=fulfillment_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
