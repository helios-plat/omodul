"""omodul.capture_payment — 手动捕获订单的支付意向。

大多数订单在 complete_checkout 时已经 inline 捕获过款(见该函数的
"reservation→sale"一步到位设计),本函数是给"仅授权未捕获"场景(如未来
接入需要两阶段收单的真实支付网关)保留的通用入口,也是运营后台手动
补捕获的兜底动作。对一个已经 captured 的 intent 再次调用会被 provider
自身的状态机拒绝(如 ManualPaymentProvider),不在本函数重复校验。

Composes:
  - oprim.ext_pay_capture

Pillars: decision_trail, cost
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_TERMINAL_ORDER_STATUSES = {"canceled", "archived"}


class CapturePaymentConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "capture_payment"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}


class CapturePaymentInput(BaseModel):
    order_id: str


async def capture_payment(
    config: CapturePaymentConfig,
    input_data: CapturePaymentInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """捕获订单绑定的支付意向。

    Args:
        config: CapturePaymentConfig。
        input_data: order_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 provider 的 capture 结果。
    """
    from obase.persistence import read_one
    from oprim.ext_pay_capture import ext_pay_capture

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — capture_payment always touches persisted order")

        order = await read_one(pool, table="customer_order", id=input_data.order_id)
        if order is None:
            raise ValueError(f"order {input_data.order_id} not found")
        if order["status"] in _TERMINAL_ORDER_STATUSES:
            raise ValueError(
                f"order {input_data.order_id} is in terminal status {order['status']!r}"
            )
        if not order["payment_provider_name"] or not order["payment_intent_id"]:
            raise ValueError(f"order {input_data.order_id} has no payment intent to capture")

        capture_result = await ext_pay_capture(
            order["payment_provider_name"], intent_id=order["payment_intent_id"]
        )
        trail.record(
            event="payment_captured",
            provider_name=order["payment_provider_name"],
            intent_id=order["payment_intent_id"],
        )

        if on_step:
            on_step({"stage": "capture_payment", "status": "done", "order_id": input_data.order_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            order_id=input_data.order_id,
            capture_result=capture_result,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
