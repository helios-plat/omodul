"""omodul.set_cart_billing_address — 把账单地址快照写到购物车。

不是客户地址簿(SPEC §4.2 add_customer_address,尚未实现)的引用,只是
本次下单的地址快照,存 JSONB,结构由本模块的 Input 定义。

Composes:
  - obase.persistence.read_one / update_one(cart 存在性校验 + 写回)

Pillars: fingerprint
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: SetCartBillingAddressConfig, input_data: SetCartBillingAddressInput
) -> str:
    """Fingerprint over cart_id + 地址字段整体。"""
    return compute_fingerprint(
        {
            "cart_id": input_data.cart_id,
            "recipient_name": input_data.recipient_name,
            "phone": input_data.phone,
            "address_line1": input_data.address_line1,
            "address_line2": input_data.address_line2,
            "city": input_data.city,
            "region_code": input_data.region_code,
            "postal_code": input_data.postal_code,
        }
    )


class SetCartBillingAddressConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "set_cart_billing_address"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class SetCartBillingAddressInput(BaseModel):
    cart_id: str
    recipient_name: str
    phone: str
    address_line1: str
    address_line2: str = ""
    city: str
    region_code: str
    postal_code: str


async def set_cart_billing_address(
    config: SetCartBillingAddressConfig,
    input_data: SetCartBillingAddressInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """把账单地址写到 cart.billing_address(JSONB)。

    Args:
        config: SetCartBillingAddressConfig。
        input_data: cart_id + 结构化地址字段。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — set_cart_billing_address always touches persisted cart"
            )

        fp = compute_fingerprint_for(config, input_data)

        cart = await read_one(pool, table="cart", id=input_data.cart_id)
        if cart is None:
            raise ValueError(f"cart {input_data.cart_id} not found")

        address = {
            "recipient_name": input_data.recipient_name,
            "phone": input_data.phone,
            "address_line1": input_data.address_line1,
            "address_line2": input_data.address_line2,
            "city": input_data.city,
            "region_code": input_data.region_code,
            "postal_code": input_data.postal_code,
        }
        await update_one(
            pool,
            table="cart",
            id=input_data.cart_id,
            data={"billing_address": json.dumps(address)},
        )
        trail.record(event="billing_address_set", cart_id=input_data.cart_id)

        if on_step:
            on_step(
                {
                    "stage": "set_cart_billing_address",
                    "status": "done",
                    "cart_id": input_data.cart_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            cart_id=input_data.cart_id,
            billing_address=address,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
