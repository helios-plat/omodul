"""omodul.authorize_payment_for_cart — 确认购物车已完成支付授权,仅冻结资金。

对齐 SPEC "仅冻结资金,不转订单"：真正的资金冻结已经在
create_payment_sessions 阶段调用 provider.authorize() 完成(每个候选
session 各自持有一个 provider 侧的授权态 intent)。本函数不重复调用
provider——它的职责是把"用户已经选定一个 session 且金额跟当前购物车一致"
这件事,在 cart 层面确认下来(cart.status → 'payment_authorized'),作为
complete_checkout 的前置门槛。如果购物车在选定 session 之后又变了(加购/
改折扣/改运费)导致金额不一致,拒绝并要求先调 update_payment_sessions
刷新,而不是静默拿旧金额去结账。

Composes:
  - obase.persistence.transaction(cart 行锁 + session 校验 + 状态推进)

Pillars: fingerprint + decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: AuthorizePaymentForCartConfig, input_data: AuthorizePaymentForCartInput
) -> str:
    """Fingerprint over cart_id。"""
    return compute_fingerprint({"cart_id": input_data.cart_id})


class AuthorizePaymentForCartConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "authorize_payment_for_cart"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}


class AuthorizePaymentForCartInput(BaseModel):
    cart_id: str


async def authorize_payment_for_cart(
    config: AuthorizePaymentForCartConfig,
    input_data: AuthorizePaymentForCartInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """校验已选定的支付会话金额跟当前购物车一致,推进 cart.status。

    Args:
        config: AuthorizePaymentForCartConfig。
        input_data: cart_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 session_id / provider_name / amount_due_cents。
    """
    from obase.persistence import transaction

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — authorize_payment_for_cart always touches persisted cart"
            )

        fp = compute_fingerprint_for(config, input_data)

        async with transaction(pool) as tx:
            cart = await tx.fetchrow(
                'SELECT * FROM "cart" WHERE id = $1 AND deleted_at IS NULL FOR UPDATE',
                input_data.cart_id,
            )
            if cart is None:
                raise ValueError(f"cart {input_data.cart_id} not found")
            if cart["status"] == "completed":
                raise ValueError(f"cart {input_data.cart_id} has already been checked out")

            session = await tx.fetchrow(
                "SELECT * FROM \"payment_session\" WHERE cart_id = $1 AND status = 'selected' "
                "AND deleted_at IS NULL",
                input_data.cart_id,
            )
            if session is None:
                raise ValueError(
                    f"no selected payment session for cart {input_data.cart_id} — "
                    "call set_payment_session first"
                )

            already_applied = await tx.fetchval(
                'SELECT COALESCE(SUM(applied_amount_cents), 0) FROM "cart_gift_card" '
                "WHERE cart_id = $1 AND deleted_at IS NULL",
                input_data.cart_id,
            )
            amount_due = max(cart["grand_total_cents"] - already_applied, 0)

            if session["amount_cents"] != amount_due:
                raise ValueError(
                    f"payment session amount ({session['amount_cents']}) is stale — "
                    f"cart currently owes {amount_due}; call update_payment_sessions first"
                )

            await tx.execute(
                "UPDATE \"cart\" SET status = 'payment_authorized', updated_at = NOW() "
                "WHERE id = $1",
                input_data.cart_id,
            )
            trail.record(
                event="payment_authorized",
                session_id=str(session["id"]),
                provider_name=session["provider_name"],
                amount_due_cents=amount_due,
            )

        if on_step:
            on_step(
                {
                    "stage": "authorize_payment_for_cart",
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
            session_id=session["id"],
            provider_name=session["provider_name"],
            amount_due_cents=amount_due,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
