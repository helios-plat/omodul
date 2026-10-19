"""omodul.delete_line_item_from_cart — 从购物车移除一行，释放锁定库存。

Composes:
  - obase.cache.DistributedLock（按 batch_id 加锁，理由同 add_line_item_to_cart）
  - obase.persistence.transaction（行软删 + 库存 reserved_qty 释放 + cart totals 写回，
    同一事务内原子提交）
  - oskill.compute_cart_subtotal + oskill.compute_cart_grand_total（全量重算）

Pillars: fingerprint + decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: DeleteLineItemConfig, input_data: DeleteLineItemInput
) -> str:
    """Fingerprint over cart_id + batch_id。"""
    return compute_fingerprint(
        {
            "cart_id": input_data.cart_id,
            "batch_id": input_data.batch_id,
        }
    )


class DeleteLineItemConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_line_item_from_cart"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "batch_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    redis_url: str = "redis://localhost:6379/0"
    lock_timeout_seconds: float = 5.0


class DeleteLineItemInput(BaseModel):
    cart_id: str
    batch_id: str


async def delete_line_item_from_cart(
    config: DeleteLineItemConfig,
    input_data: DeleteLineItemInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """移除购物车里某个 batch 对应的行，释放它占用的 reserved_qty，重算 totals。

    Args:
        config: DeleteLineItemConfig。
        input_data: cart_id / batch_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库，pool 为 None 直接判 failed。
        on_step: 进度回调，可选。

    Returns:
        标准 omodul 返回 dict；findings 含 cart totals。
    """
    from obase.cache import DistributedLock
    from obase.exceptions import LockAcquisitionError
    from obase.persistence import transaction
    from oskill import compute_cart_grand_total, compute_cart_subtotal

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — delete_line_item_from_cart always touches persisted stock"
            )

        fp = compute_fingerprint_for(config, input_data)

        async with DistributedLock(
            key=f"inventory_batch:{input_data.batch_id}",
            redis_url=config.redis_url,
            timeout_seconds=config.lock_timeout_seconds,
        ):
            trail.record(event="lock_acquired", batch_id=input_data.batch_id)

            async with transaction(pool) as tx:
                existing = await tx.fetchrow(
                    'SELECT * FROM "cart_line_item" WHERE cart_id = $1 AND batch_id = $2 '
                    "AND deleted_at IS NULL",
                    input_data.cart_id,
                    input_data.batch_id,
                )
                if existing is None:
                    raise ValueError(
                        f"no existing line item for cart {input_data.cart_id} / "
                        f"batch {input_data.batch_id}"
                    )

                await tx.execute(
                    'UPDATE "cart_line_item" SET deleted_at = NOW(), updated_at = NOW() '
                    "WHERE id = $1",
                    existing["id"],
                )
                await tx.execute(
                    'UPDATE "inventory_batch" SET reserved_qty = reserved_qty - $1, '
                    "updated_at = NOW() WHERE id = $2",
                    existing["quantity"],
                    input_data.batch_id,
                )
                trail.record(
                    event="line_item_removed",
                    line_item_id=str(existing["id"]),
                    released_qty=existing["quantity"],
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
                    "stage": "delete_line_item_from_cart",
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
