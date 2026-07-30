"""omodul.refund_payment — 对订单执行部分或全额退款,并记账到
customer_order.refunded_cents(防超额退款)。

纯支付侧动作,不碰库存(库存回补是 receive_return/fulfill_swap/
fulfill_claim 各自的职责——它们各自独立内联退款逻辑,不能调用本函数,
因为 omodul 之间禁止裸调)。

Composes:
  - obase.cache.DistributedLock(按 order_id 加锁,防止并发退款超额)
  - obase.persistence.transaction
  - oprim.ext_pay_refund

Pillars: decision_trail, cost
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class RefundPaymentConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "refund_payment"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}

    redis_url: str = "redis://localhost:6379/0"
    lock_timeout_seconds: float = 10.0


class RefundPaymentInput(BaseModel):
    order_id: str
    amount_cents: int


async def refund_payment(
    config: RefundPaymentConfig,
    input_data: RefundPaymentInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """对订单执行退款,累加 refunded_cents,拒绝超过 grand_total_cents 的退款。

    Args:
        config: RefundPaymentConfig。
        input_data: order_id / amount_cents。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 provider 的 refund 结果。
    """
    from obase.cache import DistributedLock
    from obase.exceptions import LockAcquisitionError
    from obase.persistence import transaction
    from oprim.ext_pay_refund import ext_pay_refund

    trail = Trail()

    try:
        if input_data.amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        if pool is None:
            raise ValueError("pool is required — refund_payment always touches persisted order")

        async with DistributedLock(
            key=f"order:{input_data.order_id}",
            redis_url=config.redis_url,
            timeout_seconds=config.lock_timeout_seconds,
        ):
            async with transaction(pool) as tx:
                order = await tx.fetchrow(
                    'SELECT * FROM "customer_order" WHERE id = $1 FOR UPDATE',
                    input_data.order_id,
                )
                if order is None:
                    raise ValueError(f"order {input_data.order_id} not found")
                if not order["payment_provider_name"] or not order["payment_intent_id"]:
                    raise ValueError(f"order {input_data.order_id} has no payment intent to refund")

                already_refunded = order["refunded_cents"] or 0
                if already_refunded + input_data.amount_cents > order["grand_total_cents"]:
                    raise ValueError(
                        f"refund {input_data.amount_cents} would exceed grand_total: "
                        f"already refunded {already_refunded}, grand_total "
                        f"{order['grand_total_cents']}"
                    )

                refund_result = await ext_pay_refund(
                    order["payment_provider_name"],
                    intent_id=order["payment_intent_id"],
                    amount=input_data.amount_cents,
                )
                trail.record(
                    event="payment_refunded",
                    provider_name=order["payment_provider_name"],
                    amount_cents=input_data.amount_cents,
                )

                await tx.execute(
                    'UPDATE "customer_order" SET refunded_cents = refunded_cents + $1, '
                    "updated_at = NOW() WHERE id = $2",
                    input_data.amount_cents,
                    input_data.order_id,
                )

        if on_step:
            on_step({"stage": "refund_payment", "status": "done", "order_id": input_data.order_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            order_id=input_data.order_id,
            refund_result=refund_result,
        )

    except LockAcquisitionError as exc:
        trail.record(event="lock_timeout", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": "LockAcquisitionError", "message": str(exc)},
        )
    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
