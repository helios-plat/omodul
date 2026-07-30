"""omodul.set_payment_session — 从多个候选支付会话里选定一个。

一个 cart 同一时刻只能有一个 'selected' 会话:选定目标之前,任何其它已是
'selected' 的会话打回 'authorized'。只能选 status='authorized' 的会话——
'failed'/'canceled' 的会话选不了(该 provider 本来就不可用/已作废)。

Composes:
  - obase.persistence.transaction

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: SetPaymentSessionConfig, input_data: SetPaymentSessionInput
) -> str:
    """Fingerprint over cart_id + provider_name。"""
    return compute_fingerprint(
        {"cart_id": input_data.cart_id, "provider_name": input_data.provider_name}
    )


class SetPaymentSessionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "set_payment_session"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "provider_name"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class SetPaymentSessionInput(BaseModel):
    cart_id: str
    provider_name: str


async def set_payment_session(
    config: SetPaymentSessionConfig,
    input_data: SetPaymentSessionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """选定 provider_name 对应的支付会话,其它同 cart 已选定的会话打回 authorized。

    Args:
        config: SetPaymentSessionConfig。
        input_data: cart_id / provider_name。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 session_id。
    """
    from obase.persistence import transaction

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — set_payment_session always touches persisted cart")

        fp = compute_fingerprint_for(config, input_data)

        async with transaction(pool) as tx:
            target = await tx.fetchrow(
                'SELECT * FROM "payment_session" WHERE cart_id = $1 AND provider_name = $2 '
                "AND deleted_at IS NULL FOR UPDATE",
                input_data.cart_id,
                input_data.provider_name,
            )
            if target is None:
                raise ValueError(
                    f"no payment session for cart {input_data.cart_id} / "
                    f"provider {input_data.provider_name!r}"
                )
            if target["status"] != "authorized":
                raise ValueError(
                    f"payment session for provider {input_data.provider_name!r} is not "
                    f"selectable (status={target['status']!r})"
                )

            await tx.execute(
                "UPDATE \"payment_session\" SET status = 'authorized', updated_at = NOW() "
                "WHERE cart_id = $1 AND status = 'selected' AND deleted_at IS NULL",
                input_data.cart_id,
            )
            await tx.execute(
                "UPDATE \"payment_session\" SET status = 'selected', updated_at = NOW() "
                "WHERE id = $1",
                target["id"],
            )
            trail.record(
                event="payment_session_selected",
                session_id=str(target["id"]),
                provider_name=input_data.provider_name,
            )

        if on_step:
            on_step(
                {
                    "stage": "set_payment_session",
                    "status": "done",
                    "cart_id": input_data.cart_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            session_id=target["id"],
            provider_name=input_data.provider_name,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
