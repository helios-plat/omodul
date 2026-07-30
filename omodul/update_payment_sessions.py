"""omodul.update_payment_sessions — 用当前应付金额刷新所有未终态的支付会话。

购物车总额在建会话之后可能变了(加/删行、改地址触发运费变化等),旧的
provider intent 金额就过期了。本函数对该 cart 下所有 status IN
('authorized', 'selected') 的会话重新调 provider.authorize() 换一个新
intent_id + 新金额——'selected' 也要刷新(现实场景恰恰是"用户已经选好支付
方式,购物车又变了",不是边缘情况),刷新后保持原状态不变(选定的还是选定,
不会被悄悄打回 authorized);只有 provider 重新 authorize 失败时才会降级成
'failed'(此时原本被选定的会话也不再可信,authorize_payment_for_cart 会因为
找不到有效的 selected 会话而拒绝,提示用户重新选)。不动 'canceled'/'failed'
状态的会话——那些已经是终态,刷新没有意义。

范围声明:不显式 cancel 旧 intent(多数支付网关的未确认 authorize 会自然
过期,这里只是简化处理,没有对接真实网关做验证)。

Composes:
  - obase.provider_registry.ProviderRegistry
  - obase.persistence.transaction

Pillars: fingerprint + decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: UpdatePaymentSessionsConfig, input_data: UpdatePaymentSessionsInput
) -> str:
    """Fingerprint over cart_id。"""
    return compute_fingerprint({"cart_id": input_data.cart_id})


class UpdatePaymentSessionsConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_payment_sessions"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}


class UpdatePaymentSessionsInput(BaseModel):
    cart_id: str


async def update_payment_sessions(
    config: UpdatePaymentSessionsConfig,
    input_data: UpdatePaymentSessionsInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """用当前应付金额重新 authorize 所有未终态会话。

    Args:
        config: UpdatePaymentSessionsConfig。
        input_data: cart_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 sessions(刷新结果)+ amount_due_cents。
    """
    from obase.exceptions import ProviderNotFoundError
    from obase.persistence import transaction
    from obase.provider_registry import ProviderRegistry

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — update_payment_sessions always touches persisted cart"
            )

        fp = compute_fingerprint_for(config, input_data)

        sessions: list[dict] = []

        async with transaction(pool) as tx:
            cart = await tx.fetchrow(
                'SELECT * FROM "cart" WHERE id = $1 AND deleted_at IS NULL FOR UPDATE',
                input_data.cart_id,
            )
            if cart is None:
                raise ValueError(f"cart {input_data.cart_id} not found")

            already_applied = await tx.fetchval(
                'SELECT COALESCE(SUM(applied_amount_cents), 0) FROM "cart_gift_card" '
                "WHERE cart_id = $1 AND deleted_at IS NULL",
                input_data.cart_id,
            )
            amount_due = max(cart["grand_total_cents"] - already_applied, 0)

            outstanding = await tx.fetch(
                'SELECT * FROM "payment_session" WHERE cart_id = $1 '
                "AND status IN ('authorized', 'selected') AND deleted_at IS NULL",
                input_data.cart_id,
            )

            for row in outstanding:
                try:
                    provider = ProviderRegistry.get().generic("payment", row["provider_name"])
                    result = await provider.authorize(
                        amount=amount_due,
                        currency=cart["currency"],
                        meta={"cart_id": input_data.cart_id},
                    )
                    # Preserve the row's existing status (authorized stays
                    # authorized, selected stays selected) — only a failed
                    # re-authorize demotes it.
                    status, intent_id, error_message = row["status"], result["intent_id"], None
                except ProviderNotFoundError as exc:
                    status, intent_id, error_message = "failed", None, str(exc)
                except Exception as exc:  # noqa: BLE001 — provider SDK errors are heterogeneous
                    status, intent_id, error_message = "failed", None, str(exc)

                await tx.execute(
                    'UPDATE "payment_session" SET amount_cents = $1, status = $2, '
                    "provider_intent_id = $3, error_message = $4, updated_at = NOW() "
                    "WHERE id = $5",
                    amount_due,
                    status,
                    intent_id,
                    error_message,
                    row["id"],
                )
                sessions.append(
                    {
                        "provider_name": row["provider_name"],
                        "session_id": str(row["id"]),
                        "status": status,
                    }
                )
                trail.record(
                    event="session_refreshed", provider_name=row["provider_name"], status=status
                )

        if on_step:
            on_step(
                {
                    "stage": "update_payment_sessions",
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
            sessions=sessions,
            amount_due_cents=amount_due,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
