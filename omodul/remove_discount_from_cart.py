"""omodul.remove_discount_from_cart — 撤销一张已生效的折扣,重算 totals。

Composes:
  - obase.cache.DistributedLock(按 discount_id 加锁,理由同 apply_discount_to_cart)
  - obase.persistence.transaction(软删 cart_discount + 用量回退 + totals 重算)

Pillars: fingerprint + decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: RemoveDiscountFromCartConfig, input_data: RemoveDiscountFromCartInput
) -> str:
    """Fingerprint over cart_id + discount_id。"""
    return compute_fingerprint(
        {"cart_id": input_data.cart_id, "discount_id": input_data.discount_id}
    )


class RemoveDiscountFromCartConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "remove_discount_from_cart"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "discount_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    redis_url: str = "redis://localhost:6379/0"
    lock_timeout_seconds: float = 5.0


class RemoveDiscountFromCartInput(BaseModel):
    cart_id: str
    discount_id: str


async def remove_discount_from_cart(
    config: RemoveDiscountFromCartConfig,
    input_data: RemoveDiscountFromCartInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """撤销购物车上已生效的折扣,回退 discount_rule.uses_count,重算 totals。

    Args:
        config: RemoveDiscountFromCartConfig。
        input_data: cart_id / discount_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 cart totals。
    """
    from obase.cache import DistributedLock
    from obase.exceptions import LockAcquisitionError
    from obase.persistence import transaction
    from oskill import compute_cart_grand_total

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — remove_discount_from_cart always touches persisted stock"
            )

        fp = compute_fingerprint_for(config, input_data)

        async with DistributedLock(
            key=f"discount:{input_data.discount_id}",
            redis_url=config.redis_url,
            timeout_seconds=config.lock_timeout_seconds,
        ):
            trail.record(event="lock_acquired", discount_id=input_data.discount_id)

            async with transaction(pool) as tx:
                existing = await tx.fetchrow(
                    'SELECT * FROM "cart_discount" WHERE cart_id = $1 AND discount_id = $2 '
                    "AND deleted_at IS NULL",
                    input_data.cart_id,
                    input_data.discount_id,
                )
                if existing is None:
                    raise ValueError(
                        f"discount {input_data.discount_id} is not applied to cart "
                        f"{input_data.cart_id}"
                    )

                cart = await tx.fetchrow(
                    'SELECT * FROM "cart" WHERE id = $1 AND deleted_at IS NULL',
                    input_data.cart_id,
                )
                if cart is None:
                    raise ValueError(f"cart {input_data.cart_id} not found")

                await tx.execute(
                    'UPDATE "cart_discount" SET deleted_at = NOW() WHERE id = $1', existing["id"]
                )
                await tx.execute(
                    'UPDATE "discount_rule" SET uses_count = GREATEST(uses_count - 1, 0) '
                    "WHERE discount_id = $1",
                    input_data.discount_id,
                )
                trail.record(event="discount_removed", cart_discount_id=str(existing["id"]))

                discount_cents = await tx.fetchval(
                    """
                    SELECT COALESCE(SUM(cd.applied_amount_cents), 0)
                    FROM "cart_discount" cd
                    JOIN "discount_rule" dr ON dr.discount_id = cd.discount_id
                    WHERE cd.cart_id = $1 AND cd.deleted_at IS NULL
                    AND dr.rule_type != 'free_shipping'
                    """,
                    input_data.cart_id,
                )
                has_free_shipping = await tx.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM "cart_discount" cd
                        JOIN "discount_rule" dr ON dr.discount_id = cd.discount_id
                        WHERE cd.cart_id = $1 AND cd.deleted_at IS NULL
                        AND dr.rule_type = 'free_shipping'
                    )
                    """,
                    input_data.cart_id,
                )
                effective_shipping = 0 if has_free_shipping else cart["shipping_cents"]
                grand_total = compute_cart_grand_total(
                    cart["subtotal_cents"],
                    discount=discount_cents,
                    tax=cart["tax_cents"],
                    shipping=effective_shipping,
                )
                await tx.execute(
                    'UPDATE "cart" SET discount_cents = $1, grand_total_cents = $2, '
                    "updated_at = NOW() WHERE id = $3",
                    discount_cents,
                    grand_total,
                    input_data.cart_id,
                )
                trail.record(
                    event="cart_totals_recomputed",
                    discount_cents=discount_cents,
                    grand_total=grand_total,
                )

        if on_step:
            on_step(
                {
                    "stage": "remove_discount_from_cart",
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
            discount_cents=discount_cents,
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
