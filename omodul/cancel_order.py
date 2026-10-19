"""omodul.cancel_order — 取消订单,释放库存 + 退款联动。

SPEC 原话"由 saga 编排"——完整架构下取消应该是一个 saga(先退款,退款失败
则不释放库存这类补偿步骤各自独立、可重试)。oservi.saga_composer(SPEC §5)
还没实现,本函数把"释放库存 + 退款 + 改状态"放在同一个数据库事务里
原子完成,是简化版:退款失败,整个事务回滚,库存不会被误放出去、订单
也不会被误标成已取消——比"退款失败但库存已经放出去"的不一致状态更安全,
代价是退款失败时用户必须重新触发一次 cancel_order(没有 saga 的分步重试)。

只能取消"已成交"的订单(非 draft/非终态)——草稿单用 delete_draft_order,
已经是 canceled/archived 的订单再取消没有意义。

Composes:
  - obase.cache.DistributedLock(按 order_id 加锁,防止并发重复取消)
  - obase.persistence.transaction
  - obase.provider_registry.ProviderRegistry(取 provider 调 refund)

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_NOT_CANCELABLE_STATUSES = {"draft", "canceled", "archived"}


class CancelOrderConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "cancel_order"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}

    redis_url: str = "redis://localhost:6379/0"
    lock_timeout_seconds: float = 10.0


class CancelOrderInput(BaseModel):
    order_id: str


async def cancel_order(
    config: CancelOrderConfig,
    input_data: CancelOrderInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """取消一笔已成交订单:释放库存回可售、退款、改状态,三者同一事务原子完成。

    Args:
        config: CancelOrderConfig。
        input_data: order_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 refund 结果(如有)。
    """
    from obase.cache import DistributedLock
    from obase.exceptions import LockAcquisitionError
    from obase.persistence import transaction
    from obase.provider_registry import ProviderRegistry

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — cancel_order always touches persisted order")

        async with DistributedLock(
            key=f"order:{input_data.order_id}",
            redis_url=config.redis_url,
            timeout_seconds=config.lock_timeout_seconds,
        ):
            trail.record(event="lock_acquired", order_id=input_data.order_id)

            async with transaction(pool) as tx:
                order = await tx.fetchrow(
                    'SELECT * FROM "customer_order" WHERE id = $1 FOR UPDATE',
                    input_data.order_id,
                )
                if order is None:
                    raise ValueError(f"order {input_data.order_id} not found")
                if order["status"] in _NOT_CANCELABLE_STATUSES:
                    raise ValueError(
                        f"order {input_data.order_id} cannot be canceled from status "
                        f"{order['status']!r}"
                    )

                lines = await tx.fetch(
                    'SELECT * FROM "order_line_item" WHERE order_id = $1', input_data.order_id
                )
                for line in lines:
                    await tx.execute(
                        'UPDATE "inventory_batch" SET stock_qty = stock_qty + $1, '
                        "updated_at = NOW() WHERE id = $2",
                        line["quantity"],
                        line["batch_id"],
                    )
                trail.record(event="inventory_released", lines=len(lines))

                refund_result = None
                if order["payment_provider_name"] and order["payment_intent_id"]:
                    provider = ProviderRegistry.get().generic(
                        "payment", order["payment_provider_name"]
                    )
                    refund_result = await provider.refund(
                        intent_id=order["payment_intent_id"], amount=order["grand_total_cents"]
                    )
                    trail.record(
                        event="refunded",
                        provider_name=order["payment_provider_name"],
                        amount_cents=order["grand_total_cents"],
                    )

                await tx.execute(
                    "UPDATE \"customer_order\" SET status = 'canceled', updated_at = NOW() "
                    "WHERE id = $1",
                    input_data.order_id,
                )
                trail.record(event="order_canceled", order_id=input_data.order_id)

        if on_step:
            on_step({"stage": "cancel_order", "status": "done", "order_id": input_data.order_id})

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
