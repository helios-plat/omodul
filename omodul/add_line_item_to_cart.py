"""omodul.add_line_item_to_cart — 购物车加购/改量，强绑定 batch_id，带防超卖分布式锁。

Composes:
  - obase.cache.DistributedLock（按 batch_id 加锁 —— 并发风险来自多个购物车抢
    同一批次的库存，不是同一购物车内部并发，所以锁键是 batch_id 不是 cart_id）
  - obase.persistence.transaction（批次校验 + 行更新 + 库存扣减 + 购物车 totals
    写回，四步在同一事务内原子提交；FOR UPDATE 行锁作为 Redis 锁之外的第二道防线）
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


def compute_fingerprint_for(config: AddLineItemConfig, input_data: AddLineItemInput) -> str:
    """Fingerprint over cart_id + batch_id + quantity。"""
    return compute_fingerprint(
        {
            "cart_id": input_data.cart_id,
            "batch_id": input_data.batch_id,
            "quantity": input_data.quantity,
        }
    )


class AddLineItemConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "add_line_item_to_cart"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "batch_id", "quantity"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    redis_url: str = "redis://localhost:6379/0"
    lock_timeout_seconds: float = 5.0


class AddLineItemInput(BaseModel):
    cart_id: str
    batch_id: str
    quantity: int  # 本次新增数量（不是目标总量）—— 重复调用会继续累加


async def add_line_item_to_cart(
    config: AddLineItemConfig,
    input_data: AddLineItemInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """把一个 batch 加入购物车（或对已有行叠加数量），锁定库存并重算购物车 totals。

    Args:
        config: AddLineItemConfig。
        input_data: cart_id / batch_id / 本次新增 quantity。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库（防超卖是核心职责），
            pool 为 None 直接判 failed，不做 dry-run 分支。
        on_step: 进度回调，可选。

    Returns:
        标准 omodul 返回 dict；findings 含 line_item_id / quantity / cart totals。
    """
    from obase.cache import DistributedLock
    from obase.exceptions import LockAcquisitionError
    from obase.persistence import transaction
    from obase.uuid7 import uuid7
    from oskill import compute_cart_grand_total, compute_cart_subtotal

    trail = Trail()

    try:
        if input_data.quantity <= 0:
            raise ValueError("quantity must be positive")
        if pool is None:
            raise ValueError(
                "pool is required — add_line_item_to_cart always touches persisted stock"
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
                if batch["status"] != "active" or batch["inspection_status"] != "passed":
                    raise ValueError(
                        f"batch {input_data.batch_id} is not sellable (status={batch['status']!r}, "
                        f"inspection_status={batch['inspection_status']!r})"
                    )

                available = batch["stock_qty"] - batch["reserved_qty"]
                if available < input_data.quantity:
                    raise ValueError(
                        f"insufficient stock: requested {input_data.quantity}, "
                        f"available {available}"
                    )

                cart = await tx.fetchrow(
                    'SELECT * FROM "cart" WHERE id = $1 AND deleted_at IS NULL', input_data.cart_id
                )
                if cart is None:
                    raise ValueError(f"cart {input_data.cart_id} not found")

                existing = await tx.fetchrow(
                    'SELECT * FROM "cart_line_item" WHERE cart_id = $1 AND batch_id = $2 '
                    "AND deleted_at IS NULL",
                    input_data.cart_id,
                    input_data.batch_id,
                )
                unit_price_cents = batch["retail_price_cents"]

                if existing is not None:
                    new_qty = existing["quantity"] + input_data.quantity
                    line_item_id = str(existing["id"])
                    await tx.execute(
                        'UPDATE "cart_line_item" SET quantity = $1, unit_price_cents = $2, '
                        "line_total_cents = $3, updated_at = NOW() WHERE id = $4",
                        new_qty,
                        unit_price_cents,
                        new_qty * unit_price_cents,
                        line_item_id,
                    )
                else:
                    new_qty = input_data.quantity
                    line_item_id = uuid7()
                    await tx.execute(
                        'INSERT INTO "cart_line_item" '
                        "(id, cart_id, batch_id, quantity, unit_price_cents, line_total_cents) "
                        "VALUES ($1, $2, $3, $4, $5, $6)",
                        line_item_id,
                        input_data.cart_id,
                        input_data.batch_id,
                        new_qty,
                        unit_price_cents,
                        new_qty * unit_price_cents,
                    )

                await tx.execute(
                    'UPDATE "inventory_batch" SET reserved_qty = reserved_qty + $1, '
                    "updated_at = NOW() WHERE id = $2",
                    input_data.quantity,
                    input_data.batch_id,
                )

                trail.record(
                    event="line_item_upserted", line_item_id=str(line_item_id), quantity=new_qty
                )

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
                {"stage": "add_line_item_to_cart", "status": "done", "cart_id": input_data.cart_id}
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            line_item_id=line_item_id,
            quantity=new_qty,
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
