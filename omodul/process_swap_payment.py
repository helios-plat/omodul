"""omodul.process_swap_payment — 处理换货差价:补差价走 authorize+capture,
应退差价走 refund(用原订单的 payment_provider_name)。

price_difference_cents > 0 表示客户需要补差价(新品比退货贵);< 0 表示
应退差价给客户(退货比新品贵);create_swap 阶段差价为 0 时 payment_status
直接是 not_required,不需要也不允许调用本函数(SPEC 没给"无需付款"场景
下调用本函数的语义,视为调用方错误)。

Composes:
  - obase.persistence.transaction
  - oprim.ext_pay_authorize / ext_pay_capture / ext_pay_refund

Pillars: decision_trail, cost
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class ProcessSwapPaymentConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "process_swap_payment"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}


class ProcessSwapPaymentInput(BaseModel):
    swap_id: str


async def process_swap_payment(
    config: ProcessSwapPaymentConfig,
    input_data: ProcessSwapPaymentInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """处理换货差价(补款走 authorize+capture,退款走 refund)。

    Args:
        config: ProcessSwapPaymentConfig。
        input_data: swap_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 payment_result。
    """
    from obase.persistence import transaction
    from oprim.ext_pay_authorize import ext_pay_authorize
    from oprim.ext_pay_capture import ext_pay_capture
    from oprim.ext_pay_refund import ext_pay_refund

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — process_swap_payment always touches persisted swap"
            )

        async with transaction(pool) as tx:
            swap = await tx.fetchrow(
                'SELECT * FROM "swap" WHERE id = $1 FOR UPDATE', input_data.swap_id
            )
            if swap is None:
                raise ValueError(f"swap {input_data.swap_id} not found")
            if swap["payment_status"] != "not_paid":
                raise ValueError(
                    f"swap {input_data.swap_id} payment_status is "
                    f"{swap['payment_status']!r}, expected 'not_paid'"
                )

            order = await tx.fetchrow(
                'SELECT * FROM "customer_order" WHERE id = $1 FOR UPDATE', swap["order_id"]
            )
            if order is None:
                raise ValueError(f"order {swap['order_id']} not found")
            if not order["payment_provider_name"]:
                raise ValueError(f"order {swap['order_id']} has no payment provider")

            diff = swap["price_difference_cents"]
            if diff > 0:
                auth = await ext_pay_authorize(
                    order["payment_provider_name"],
                    amount=diff,
                    currency=order["currency"],
                    meta={"swap_id": input_data.swap_id},
                )
                payment_result = await ext_pay_capture(
                    order["payment_provider_name"], intent_id=auth["intent_id"]
                )
                trail.record(
                    event="swap_surcharge_captured",
                    swap_id=input_data.swap_id,
                    amount_cents=diff,
                )
            else:
                refund_amount = -diff
                already_refunded = order["refunded_cents"] or 0
                if already_refunded + refund_amount > order["grand_total_cents"]:
                    raise ValueError(
                        f"refund {refund_amount} would exceed grand_total: already refunded "
                        f"{already_refunded}, grand_total {order['grand_total_cents']}"
                    )
                payment_result = await ext_pay_refund(
                    order["payment_provider_name"],
                    intent_id=order["payment_intent_id"],
                    amount=refund_amount,
                )
                await tx.execute(
                    'UPDATE "customer_order" SET refunded_cents = refunded_cents + $1, '
                    "updated_at = NOW() WHERE id = $2",
                    refund_amount,
                    swap["order_id"],
                )
                trail.record(
                    event="swap_difference_refunded",
                    swap_id=input_data.swap_id,
                    amount_cents=refund_amount,
                )

            await tx.execute(
                "UPDATE \"swap\" SET payment_status = 'paid', updated_at = NOW() WHERE id = $1",
                input_data.swap_id,
            )

        if on_step:
            on_step(
                {
                    "stage": "process_swap_payment",
                    "status": "done",
                    "swap_id": input_data.swap_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            swap_id=input_data.swap_id,
            payment_result=payment_result,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
