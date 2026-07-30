"""omodul.receive_return — 收货入库 + 算退款额 + 执行退款(RMA 收尾三步合一)。

SPEC 原话把"入库加库存 → 算退款额 → 执行退款"三步定义为 receive_return
一个元素的职责(create_return_request 只管申请,不动库存/不退款)。三步
在同一事务里原子完成:任何一步失败(尤其 provider.refund),库存回补和
订单 refunded_cents 都不会被误写——同 cancel_order 的"没有 saga 前的
简化版原子事务"设计取向一致。

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


class ReceiveReturnConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "receive_return"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}


class ReceiveReturnInput(BaseModel):
    return_request_id: str


async def receive_return(
    config: ReceiveReturnConfig,
    input_data: ReceiveReturnInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """收货入库 + 算退款额 + 执行退款。

    Args:
        config: ReceiveReturnConfig。
        input_data: return_request_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 refund_amount_cents。
    """
    from obase.persistence import transaction
    from oprim.ext_pay_refund import ext_pay_refund

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — receive_return always touches persisted return_request"
            )

        async with transaction(pool) as tx:
            ret = await tx.fetchrow(
                'SELECT * FROM "return_request" WHERE id = $1 FOR UPDATE',
                input_data.return_request_id,
            )
            if ret is None:
                raise ValueError(f"return_request {input_data.return_request_id} not found")
            if ret["status"] != "requested":
                raise ValueError(
                    f"return_request {input_data.return_request_id} cannot be received from "
                    f"status {ret['status']!r}"
                )

            order = await tx.fetchrow(
                'SELECT * FROM "customer_order" WHERE id = $1 FOR UPDATE', ret["order_id"]
            )
            if order is None:
                raise ValueError(f"order {ret['order_id']} not found")
            if not order["payment_provider_name"] or not order["payment_intent_id"]:
                raise ValueError(f"order {ret['order_id']} has no payment intent to refund")

            items = json.loads(ret["items"])
            refund_amount_cents = 0
            for item in items:
                line = await tx.fetchrow(
                    'SELECT * FROM "order_line_item" WHERE id = $1', item["order_line_item_id"]
                )
                if line is None:
                    raise ValueError(f"order_line_item {item['order_line_item_id']} not found")
                refund_amount_cents += line["unit_price_cents"] * item["quantity"]
                await tx.execute(
                    'UPDATE "inventory_batch" SET stock_qty = stock_qty + $1, '
                    "updated_at = NOW() WHERE id = $2",
                    item["quantity"],
                    line["batch_id"],
                )
            trail.record(event="inventory_restocked", items=len(items))

            already_refunded = order["refunded_cents"] or 0
            if already_refunded + refund_amount_cents > order["grand_total_cents"]:
                raise ValueError(
                    f"refund {refund_amount_cents} would exceed grand_total: already refunded "
                    f"{already_refunded}, grand_total {order['grand_total_cents']}"
                )

            refund_result = await ext_pay_refund(
                order["payment_provider_name"],
                intent_id=order["payment_intent_id"],
                amount=refund_amount_cents,
            )
            trail.record(
                event="payment_refunded",
                provider_name=order["payment_provider_name"],
                amount_cents=refund_amount_cents,
            )

            await tx.execute(
                'UPDATE "customer_order" SET refunded_cents = refunded_cents + $1, '
                "updated_at = NOW() WHERE id = $2",
                refund_amount_cents,
                ret["order_id"],
            )
            await tx.execute(
                "UPDATE \"return_request\" SET status = 'refunded', "
                "refund_amount_cents = $1, updated_at = NOW() WHERE id = $2",
                refund_amount_cents,
                input_data.return_request_id,
            )
            trail.record(event="return_received", return_request_id=input_data.return_request_id)

        if on_step:
            on_step(
                {
                    "stage": "receive_return",
                    "status": "done",
                    "return_request_id": input_data.return_request_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            return_request_id=input_data.return_request_id,
            refund_amount_cents=refund_amount_cents,
            refund_result=refund_result,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
