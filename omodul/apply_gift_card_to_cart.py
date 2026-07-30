"""omodul.apply_gift_card_to_cart — 用礼品卡余额抵扣购物车应付金额。

Composes:
  - obase.cache.DistributedLock(按礼品卡 code 加锁,防止同一张卡被两个购物车
    并发核销导致余额透支)
  - obase.persistence.transaction(卡状态校验 + 余额分摊 + 卡余额扣减 + 关联行
    写入,同一事务内原子提交;gift_card 行锁作为第二道防线)
  - oskill.allocate_gift_card_balance

Pillars: fingerprint + decision_trail

设计要点:cart.grand_total_cents 不含礼品卡抵扣(礼品卡核销是"支付层"的事,
不是"计价层"的事);应付金额 = grand_total_cents − Σ 该购物车已核销礼品卡
金额,每次调用时现算,不落一个冗余列到 cart 表。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: ApplyGiftCardToCartConfig, input_data: ApplyGiftCardToCartInput
) -> str:
    """Fingerprint over cart_id + code。"""
    return compute_fingerprint({"cart_id": input_data.cart_id, "code": input_data.code})


class ApplyGiftCardToCartConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "apply_gift_card_to_cart"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "code"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    redis_url: str = "redis://localhost:6379/0"
    lock_timeout_seconds: float = 5.0


class ApplyGiftCardToCartInput(BaseModel):
    cart_id: str
    code: str


async def apply_gift_card_to_cart(
    config: ApplyGiftCardToCartConfig,
    input_data: ApplyGiftCardToCartInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """核销礼品卡余额抵扣购物车应付金额(封顶到应付金额,不透支卡余额)。

    Args:
        config: ApplyGiftCardToCartConfig。
        input_data: cart_id / code。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 gift_card_id / applied_cents / amount_due_cents。
    """
    from obase.cache import DistributedLock
    from obase.exceptions import LockAcquisitionError
    from obase.persistence import transaction
    from obase.uuid7 import uuid7
    from oskill import allocate_gift_card_balance

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — apply_gift_card_to_cart always touches persisted stock"
            )

        fp = compute_fingerprint_for(config, input_data)

        async with DistributedLock(
            key=f"gift_card:{input_data.code}",
            redis_url=config.redis_url,
            timeout_seconds=config.lock_timeout_seconds,
        ):
            trail.record(event="lock_acquired", code=input_data.code)

            async with transaction(pool) as tx:
                gift_card = await tx.fetchrow(
                    'SELECT * FROM "gift_card" WHERE code = $1 AND deleted_at IS NULL FOR UPDATE',
                    input_data.code,
                )
                if gift_card is None:
                    raise ValueError(f"gift card code {input_data.code!r} not found")
                if gift_card["status"] != "active":
                    raise ValueError(f"gift card code {input_data.code!r} is not active")
                if gift_card["expires_at"] is not None and gift_card["expires_at"] < datetime.now(
                    UTC
                ):
                    raise ValueError(f"gift card code {input_data.code!r} has expired")
                if gift_card["balance_cents"] <= 0:
                    raise ValueError(f"gift card code {input_data.code!r} has no remaining balance")

                cart = await tx.fetchrow(
                    'SELECT * FROM "cart" WHERE id = $1 AND deleted_at IS NULL',
                    input_data.cart_id,
                )
                if cart is None:
                    raise ValueError(f"cart {input_data.cart_id} not found")

                existing_application = await tx.fetchrow(
                    'SELECT 1 FROM "cart_gift_card" WHERE cart_id = $1 AND gift_card_id = $2 '
                    "AND deleted_at IS NULL",
                    input_data.cart_id,
                    gift_card["id"],
                )
                if existing_application is not None:
                    raise ValueError(
                        f"gift card {input_data.code!r} is already applied to this cart"
                    )

                already_applied = await tx.fetchval(
                    'SELECT COALESCE(SUM(applied_amount_cents), 0) FROM "cart_gift_card" '
                    "WHERE cart_id = $1 AND deleted_at IS NULL",
                    input_data.cart_id,
                )
                amount_due_before = max(cart["grand_total_cents"] - already_applied, 0)

                allocation = allocate_gift_card_balance(
                    amount_due_before, card_balance=gift_card["balance_cents"]
                )
                applied_cents = allocation["applied_cents"]

                cart_gift_card_id = uuid7()
                await tx.execute(
                    'INSERT INTO "cart_gift_card" '
                    "(id, cart_id, gift_card_id, applied_amount_cents) VALUES ($1, $2, $3, $4)",
                    cart_gift_card_id,
                    input_data.cart_id,
                    gift_card["id"],
                    applied_cents,
                )
                await tx.execute(
                    'UPDATE "gift_card" SET balance_cents = balance_cents - $1, '
                    "updated_at = NOW() WHERE id = $2",
                    applied_cents,
                    gift_card["id"],
                )
                trail.record(
                    event="gift_card_applied",
                    cart_gift_card_id=str(cart_gift_card_id),
                    applied_cents=applied_cents,
                )

        if on_step:
            on_step(
                {
                    "stage": "apply_gift_card_to_cart",
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
            gift_card_id=gift_card["id"],
            applied_cents=applied_cents,
            amount_due_cents=allocation["remaining_cart_total_cents"],
            remaining_card_balance_cents=allocation["remaining_card_balance_cents"],
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
