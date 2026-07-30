"""omodul.complete_checkout — 购物车转订单的最高级事务。

对齐 SPEC "拿锁 → 二次验价防超卖 → create_inventory_reservation → 授权支付
→ Cart 转 Order → 发事件"，在本仓库的简化批次模型下逐条落地：

  - 拿锁：DistributedLock 按 cart_id（防止同一购物车被并发结账两次）。
  - 二次验价防超卖：重新核对已选定 session 的金额是否还等于当前应付
    （authorize_payment_for_cart 校验过一次，这里在拿到锁、真正扣款前
    再核一次——之间的窗口理论上购物车不该再变，但不能假设）。
  - create_inventory_reservation：本模型从 add_line_item_to_cart 起就已经
    把库存以 inventory_batch.reserved_qty 的形式预留了，不是等到结账才建
    预留记录；结账这一步的等效动作是把"预留"转成"永久出库"——
    stock_qty 和 reserved_qty 同时按 quantity 扣减（可售总量真的减少了，
    不是仅仅解除预留）。
  - 授权支付：不重复调 provider.authorize()（已在 create_payment_sessions
    做过），这里调 provider.capture() 真正扣款；capture 失败整个事务回滚，
    库存扣减和订单创建都不会落地。
  - Cart 转 Order：customer_order/order_line_item 是从 cart/cart_line_item
    复制的快照，不是引用（cart 后续可能被清理，订单必须留住下单当时的
    真相）。
  - 发事件：oservi 的 event_webhook_dispatcher（SPEC §5）尚未实现，本函数
    不假装派发了事件——decision_trail 里的 order_created 记录是目前唯一
    的审计手段。

Composes:
  - obase.cache.DistributedLock + obase.persistence.transaction
  - obase.provider_registry.ProviderRegistry(取选定 session 的 provider,调 capture)

Pillars: fingerprint + decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: CompleteCheckoutConfig, input_data: CompleteCheckoutInput
) -> str:
    """Fingerprint over cart_id。"""
    return compute_fingerprint({"cart_id": input_data.cart_id})


class CompleteCheckoutConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "complete_checkout"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    redis_url: str = "redis://localhost:6379/0"
    lock_timeout_seconds: float = 10.0


class CompleteCheckoutInput(BaseModel):
    cart_id: str


async def complete_checkout(
    config: CompleteCheckoutConfig,
    input_data: CompleteCheckoutInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """把已授权支付的购物车转成订单:扣库存、扣款、建订单、关车。

    Args:
        config: CompleteCheckoutConfig。
        input_data: cart_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 order_id / totals / capture 结果。
    """
    from obase.cache import DistributedLock
    from obase.exceptions import LockAcquisitionError
    from obase.persistence import transaction
    from obase.provider_registry import ProviderRegistry
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — complete_checkout always touches persisted cart")

        fp = compute_fingerprint_for(config, input_data)

        async with DistributedLock(
            key=f"checkout:{input_data.cart_id}",
            redis_url=config.redis_url,
            timeout_seconds=config.lock_timeout_seconds,
        ):
            trail.record(event="lock_acquired", cart_id=input_data.cart_id)

            async with transaction(pool) as tx:
                cart = await tx.fetchrow(
                    'SELECT * FROM "cart" WHERE id = $1 AND deleted_at IS NULL FOR UPDATE',
                    input_data.cart_id,
                )
                if cart is None:
                    raise ValueError(f"cart {input_data.cart_id} not found")
                if cart["status"] != "payment_authorized":
                    raise ValueError(
                        f"cart {input_data.cart_id} is not ready for checkout "
                        f"(status={cart['status']!r}) — call authorize_payment_for_cart first"
                    )

                session = await tx.fetchrow(
                    "SELECT * FROM \"payment_session\" WHERE cart_id = $1 AND status = 'selected' "
                    "AND deleted_at IS NULL FOR UPDATE",
                    input_data.cart_id,
                )
                if session is None:
                    raise ValueError(f"no selected payment session for cart {input_data.cart_id}")

                already_applied = await tx.fetchval(
                    'SELECT COALESCE(SUM(applied_amount_cents), 0) FROM "cart_gift_card" '
                    "WHERE cart_id = $1 AND deleted_at IS NULL",
                    input_data.cart_id,
                )
                amount_due = max(cart["grand_total_cents"] - already_applied, 0)
                if session["amount_cents"] != amount_due:
                    raise ValueError(
                        f"payment session amount ({session['amount_cents']}) no longer matches "
                        f"cart total ({amount_due}) — call update_payment_sessions and "
                        "authorize_payment_for_cart again"
                    )
                trail.record(event="revalidated_totals", amount_due_cents=amount_due)

                line_rows = await tx.fetch(
                    'SELECT * FROM "cart_line_item" WHERE cart_id = $1 AND deleted_at IS NULL',
                    input_data.cart_id,
                )
                if not line_rows:
                    raise ValueError(f"cart {input_data.cart_id} has no line items")

                for line in line_rows:
                    batch = await tx.fetchrow(
                        'SELECT * FROM "inventory_batch" WHERE id = $1 FOR UPDATE',
                        line["batch_id"],
                    )
                    if batch is None or batch["reserved_qty"] < line["quantity"]:
                        raise ValueError(
                            f"batch {line['batch_id']} reservation inconsistent with cart line "
                            f"{line['id']} — cannot complete checkout"
                        )
                    await tx.execute(
                        'UPDATE "inventory_batch" SET stock_qty = stock_qty - $1, '
                        "reserved_qty = reserved_qty - $1, updated_at = NOW() WHERE id = $2",
                        line["quantity"],
                        line["batch_id"],
                    )
                trail.record(event="inventory_converted_to_sale", lines=len(line_rows))

                provider = ProviderRegistry.get().generic("payment", session["provider_name"])
                capture_result = await provider.capture(intent_id=session["provider_intent_id"])
                trail.record(
                    event="payment_captured",
                    provider_name=session["provider_name"],
                    intent_id=session["provider_intent_id"],
                )

                order_id = uuid7()
                await tx.execute(
                    'INSERT INTO "customer_order" '
                    "(id, cart_id, customer_id, region_code, currency, status, subtotal_cents, "
                    "discount_cents, tax_cents, shipping_cents, grand_total_cents, "
                    "payment_provider_name, payment_intent_id, billing_address, shipping_address) "
                    "VALUES "
                    "($1, $2, $3, $4, $5, 'pending', $6, $7, $8, $9, $10, $11, $12, $13, $14)",
                    order_id,
                    input_data.cart_id,
                    cart["customer_id"],
                    cart["region_code"],
                    cart["currency"],
                    cart["subtotal_cents"],
                    cart["discount_cents"],
                    cart["tax_cents"],
                    cart["shipping_cents"],
                    cart["grand_total_cents"],
                    session["provider_name"],
                    session["provider_intent_id"],
                    cart["billing_address"],
                    cart["shipping_address"],
                )
                for line in line_rows:
                    await tx.execute(
                        'INSERT INTO "order_line_item" '
                        "(id, order_id, batch_id, quantity, unit_price_cents, line_total_cents) "
                        "VALUES ($1, $2, $3, $4, $5, $6)",
                        uuid7(),
                        order_id,
                        line["batch_id"],
                        line["quantity"],
                        line["unit_price_cents"],
                        line["line_total_cents"],
                    )

                await tx.execute(
                    "UPDATE \"cart\" SET status = 'completed', updated_at = NOW() WHERE id = $1",
                    input_data.cart_id,
                )
                trail.record(event="order_created", order_id=str(order_id))

        if on_step:
            on_step({"stage": "complete_checkout", "status": "done", "cart_id": input_data.cart_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            order_id=order_id,
            grand_total_cents=cart["grand_total_cents"],
            capture_result=capture_result,
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
