"""omodul.create_payment_sessions — 对多个候选 provider 各建一条支付会话。

对齐 SPEC "多态调 ext_pay_authorize 生成多 Session":给用户在结账页展示的
每个可选支付方式各调一次 provider.authorize(),生成候选 session,供
set_payment_session 挑一个确认。单个 provider 失败(未注册/authorize 抛错)
只让那一条 session 落 status='failed',不拖累其它 provider——多态调用要的
就是"部分成功也能继续"。

已存在的活跃 session(同 cart_id+provider_name)不重复创建,直接跳过并在
findings 里标记,重新授权走 update_payment_sessions,不是这里的职责。

Composes:
  - obase.provider_registry.ProviderRegistry(取 "payment" category 下的 provider)
  - obase.persistence.transaction(cart 行锁 + 应付金额计算 + session 写入)

Pillars: fingerprint + decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: CreatePaymentSessionsConfig, input_data: CreatePaymentSessionsInput
) -> str:
    """Fingerprint over cart_id + provider_names。"""
    return compute_fingerprint(
        {"cart_id": input_data.cart_id, "provider_names": sorted(input_data.provider_names)}
    )


class CreatePaymentSessionsConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_payment_sessions"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "provider_names"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}


class CreatePaymentSessionsInput(BaseModel):
    cart_id: str
    provider_names: list[str]


async def create_payment_sessions(
    config: CreatePaymentSessionsConfig,
    input_data: CreatePaymentSessionsInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """为每个候选 provider 建一条支付会话(已有活跃会话则跳过)。

    Args:
        config: CreatePaymentSessionsConfig。
        input_data: cart_id / provider_names。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 sessions(每个 provider 的结果)+ amount_due_cents。
    """
    from obase.exceptions import ProviderNotFoundError
    from obase.persistence import transaction
    from obase.provider_registry import ProviderRegistry
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.provider_names:
            raise ValueError("provider_names must not be empty")
        if pool is None:
            raise ValueError(
                "pool is required — create_payment_sessions always touches persisted cart"
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

            for provider_name in input_data.provider_names:
                existing = await tx.fetchrow(
                    'SELECT id, status FROM "payment_session" '
                    "WHERE cart_id = $1 AND provider_name = $2 AND deleted_at IS NULL",
                    input_data.cart_id,
                    provider_name,
                )
                if existing is not None:
                    sessions.append(
                        {
                            "provider_name": provider_name,
                            "session_id": str(existing["id"]),
                            "status": existing["status"],
                            "already_existed": True,
                        }
                    )
                    continue

                try:
                    provider = ProviderRegistry.get().generic("payment", provider_name)
                    result = await provider.authorize(
                        amount=amount_due,
                        currency=cart["currency"],
                        meta={"cart_id": input_data.cart_id},
                    )
                    status, intent_id, error_message = "authorized", result["intent_id"], None
                except ProviderNotFoundError as exc:
                    status, intent_id, error_message = "failed", None, str(exc)
                except Exception as exc:  # noqa: BLE001 — provider SDK errors are heterogeneous
                    status, intent_id, error_message = "failed", None, str(exc)

                session_id = uuid7()
                await tx.execute(
                    'INSERT INTO "payment_session" '
                    "(id, cart_id, provider_name, amount_cents, currency, status, "
                    "provider_intent_id, error_message) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    session_id,
                    input_data.cart_id,
                    provider_name,
                    amount_due,
                    cart["currency"],
                    status,
                    intent_id,
                    error_message,
                )
                sessions.append(
                    {
                        "provider_name": provider_name,
                        "session_id": str(session_id),
                        "status": status,
                        "already_existed": False,
                    }
                )
                trail.record(event="session_created", provider_name=provider_name, status=status)

        if on_step:
            on_step(
                {
                    "stage": "create_payment_sessions",
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
