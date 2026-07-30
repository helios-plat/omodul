"""omodul.update_line_item_in_cart — 把购物车某行数量改到目标值（非累加）。

与 add_line_item_to_cart 的语义互补：add 是"再加 N 件"，本函数是"改到 N 件"
（常见于购物车页面用户直接编辑数量输入框）。数量改为 0 应调用
delete_line_item_from_cart，本函数拒绝 0（避免两个入口语义重叠）。

Composes:
  - obase.cache.DistributedLock（按 batch_id 加锁，理由同 add_line_item_to_cart）
  - obase.persistence.transaction（行校验 + 库存差量调整 + 行更新 + cart totals 写回，
    同一事务内原子提交；FOR UPDATE 行锁作为 Redis 锁之外的第二道防线）
  - oskill.compute_cart_subtotal + oskill.compute_cart_grand_total（全量重算）

Pillars: fingerprint + decision_trail

范围声明（本轮显式排除，对齐已确认的 vertical slice 范围）：
  - 不做跨仓结算；shipping_cents 从 cart 现有行透传，不在本函数内计算。
  - 不做折扣 / 税费引擎；discount_cents / tax_cents 同样透传现值（本轮通常为 0）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(config: UpdateLineItemConfig, input_data: UpdateLineItemInput) -> str:
    """Fingerprint over cart_id + batch_id + quantity。"""
    return compute_fingerprint(
        {
            "cart_id": input_data.cart_id,
            "batch_id": input_data.batch_id,
            "quantity": input_data.quantity,
        }
    )


class UpdateLineItemConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_line_item_in_cart"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "batch_id", "quantity"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    redis_url: str = "redis://localhost:6379/0"
    lock_timeout_seconds: float = 5.0


class UpdateLineItemInput(BaseModel):
    cart_id: str
    batch_id: str
    quantity: int  # 目标总量（不是增量）——必须 > 0，改到 0 请用 delete_line_item_from_cart


async def update_line_item_in_cart(
    config: UpdateLineItemConfig,
    input_data: UpdateLineItemInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """把购物车里某个 batch 对应行的数量改到 input_data.quantity，重算库存占用与 totals。

    Args:
        config: UpdateLineItemConfig。
        input_data: cart_id / batch_id / 目标 quantity。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库，pool 为 None 直接判 failed。
        on_step: 进度回调，可选。

    Returns:
        标准 omodul 返回 dict；findings 含 line_item_id / quantity / cart totals。
    """
    from obase.cache import DistributedLock
    from obase.exceptions import LockAcquisitionError
    from obase.persistence import transaction
    from oskill import compute_cart_grand_total, compute_cart_subtotal

    trail = Trail()

    try:
        if input_data.quantity <= 0:
            raise ValueError(
                "quantity must be positive — use delete_line_item_from_cart to remove a line"
            )
        if pool is None:
            raise ValueError(
                "pool is required — update_line_item_in_cart always touches persisted stock"
            )

        fp = compute_fingerprint_for(config, input_data)

        async with DistributedLock(
            key=f"inventory_batch:{input_data.batch_id}",
            redis_url=config.redis_url,
            timeout_seconds=config.lock_timeout_seconds,
        ):
            trail.record(event="lock_acquired", batch_id=input_data.batch_id)

            async with transaction(pool) as tx:
                batch = await tx.fetchrow(
                    'SELECT * FROM "inventory_batch" WHERE id = $1 '
                    "AND deleted_at IS NULL FOR UPDATE",
                    input_data.batch_id,
                )
                if batch is None:
                    raise ValueError(f"batch {input_data.batch_id} not found")

                existing = await tx.fetchrow(
                    'SELECT * FROM "cart_line_item" WHERE cart_id = $1 AND batch_id = $2 '
                    "AND deleted_at IS NULL",
                    input_data.cart_id,
                    input_data.batch_id,
                )
                if existing is None:
                    raise ValueError(
                        f"no existing line item for cart {input_data.cart_id} / "
                        f"batch {input_data.batch_id} — use add_line_item_to_cart first"
                    )

                delta = input_data.quantity - existing["quantity"]
                if delta > 0:
                    available = batch["stock_qty"] - batch["reserved_qty"]
                    if available < delta:
                        raise ValueError(
                            f"insufficient stock: requested +{delta}, available {available}"
                        )

                unit_price_cents = batch["retail_price_cents"]
                line_item_id = str(existing["id"])
                await tx.execute(
                    'UPDATE "cart_line_item" SET quantity = $1, unit_price_cents = $2, '
                    "line_total_cents = $3, updated_at = NOW() WHERE id = $4",
                    input_data.quantity,
                    unit_price_cents,
                    input_data.quantity * unit_price_cents,
                    line_item_id,
                )

                if delta != 0:
                    await tx.execute(
                        'UPDATE "inventory_batch" SET reserved_qty = reserved_qty + $1, '
                        "updated_at = NOW() WHERE id = $2",
                        delta,
                        input_data.batch_id,
                    )

                trail.record(
                    event="line_item_updated",
                    line_item_id=line_item_id,
                    quantity=input_data.quantity,
                    delta=delta,
                )

                cart = await tx.fetchrow(
                    'SELECT * FROM "cart" WHERE id = $1 AND deleted_at IS NULL',
                    input_data.cart_id,
                )
                if cart is None:
                    raise ValueError(f"cart {input_data.cart_id} not found")

                line_rows = await tx.fetch(
                    'SELECT line_total_cents FROM "cart_line_item" WHERE cart_id = $1 '
                    "AND deleted_at IS NULL",
                    input_data.cart_id,
                )
                subtotal = compute_cart_subtotal([dict(r) for r in line_rows])
                grand_total = compute_cart_grand_total(
                    subtotal,
                    discount=cart["discount_cents"],
                    tax=cart["tax_cents"],
                    shipping=cart["shipping_cents"],
                )

                await tx.execute(
                    'UPDATE "cart" SET subtotal_cents = $1, grand_total_cents = $2, '
                    "updated_at = NOW() WHERE id = $3",
                    subtotal,
                    grand_total,
                    input_data.cart_id,
                )
                trail.record(
                    event="cart_totals_recomputed", subtotal=subtotal, grand_total=grand_total
                )

        if on_step:
            on_step(
                {
                    "stage": "update_line_item_in_cart",
                    "status": "done",
                    "cart_id": input_data.cart_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            line_item_id=line_item_id,
            quantity=input_data.quantity,
            unit_price_cents=unit_price_cents,
            subtotal_cents=subtotal,
            grand_total_cents=grand_total,
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
