"""omodul.remove_gift_card_from_cart — 撤销礼品卡核销,余额补回。

Composes:
  - obase.cache.DistributedLock(按 gift_card_id 加锁,理由同 apply_gift_card_to_cart)
  - obase.persistence.transaction(软删 cart_gift_card + 余额补回,同一事务内原子提交;
    gift_card.balance_cents 有 DB CHECK(<= initial_balance_cents)兜底,补多了会被拒)

Pillars: fingerprint + decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: RemoveGiftCardFromCartConfig, input_data: RemoveGiftCardFromCartInput
) -> str:
    """Fingerprint over cart_id + gift_card_id。"""
    return compute_fingerprint(
        {"cart_id": input_data.cart_id, "gift_card_id": input_data.gift_card_id}
    )


class RemoveGiftCardFromCartConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "remove_gift_card_from_cart"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "gift_card_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    redis_url: str = "redis://localhost:6379/0"
    lock_timeout_seconds: float = 5.0


class RemoveGiftCardFromCartInput(BaseModel):
    cart_id: str
    gift_card_id: str


async def remove_gift_card_from_cart(
    config: RemoveGiftCardFromCartConfig,
    input_data: RemoveGiftCardFromCartInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """撤销购物车上已核销的礼品卡,把核销额补回卡余额。

    Args:
        config: RemoveGiftCardFromCartConfig。
        input_data: cart_id / gift_card_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 amount_due_cents。
    """
    from obase.cache import DistributedLock
    from obase.exceptions import LockAcquisitionError
    from obase.persistence import transaction

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — remove_gift_card_from_cart always touches persisted stock"
            )

        fp = compute_fingerprint_for(config, input_data)

        async with DistributedLock(
            key=f"gift_card:{input_data.gift_card_id}",
            redis_url=config.redis_url,
            timeout_seconds=config.lock_timeout_seconds,
        ):
            trail.record(event="lock_acquired", gift_card_id=input_data.gift_card_id)

            async with transaction(pool) as tx:
                existing = await tx.fetchrow(
                    'SELECT * FROM "cart_gift_card" WHERE cart_id = $1 AND gift_card_id = $2 '
                    "AND deleted_at IS NULL",
                    input_data.cart_id,
                    input_data.gift_card_id,
                )
                if existing is None:
                    raise ValueError(
                        f"gift card {input_data.gift_card_id} is not applied to cart "
                        f"{input_data.cart_id}"
                    )

                cart = await tx.fetchrow(
                    'SELECT * FROM "cart" WHERE id = $1 AND deleted_at IS NULL',
                    input_data.cart_id,
                )
                if cart is None:
                    raise ValueError(f"cart {input_data.cart_id} not found")

                await tx.execute(
                    'UPDATE "cart_gift_card" SET deleted_at = NOW() WHERE id = $1', existing["id"]
                )
                await tx.execute(
                    'UPDATE "gift_card" SET balance_cents = balance_cents + $1, '
                    "updated_at = NOW() WHERE id = $2",
                    existing["applied_amount_cents"],
                    input_data.gift_card_id,
                )
                trail.record(
                    event="gift_card_removed",
                    cart_gift_card_id=str(existing["id"]),
                    refunded_cents=existing["applied_amount_cents"],
                )

                remaining_applied = await tx.fetchval(
                    'SELECT COALESCE(SUM(applied_amount_cents), 0) FROM "cart_gift_card" '
                    "WHERE cart_id = $1 AND deleted_at IS NULL",
                    input_data.cart_id,
                )
                amount_due_cents = max(cart["grand_total_cents"] - remaining_applied, 0)

        if on_step:
            on_step(
                {
                    "stage": "remove_gift_card_from_cart",
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
            amount_due_cents=amount_due_cents,
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
