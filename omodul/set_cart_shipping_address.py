"""omodul.set_cart_shipping_address — 把收货地址快照写到购物车。

镜像 set_cart_billing_address,唯一区别是写 cart.shipping_address 列
(收货地址后续会被 SPEC §4.7 complete_checkout / 履约域用到,目前只落库)。

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
    config: SetCartShippingAddressConfig, input_data: SetCartShippingAddressInput
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


class SetCartShippingAddressConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "set_cart_shipping_address"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class SetCartShippingAddressInput(BaseModel):
    cart_id: str
    recipient_name: str
    phone: str
    address_line1: str
    address_line2: str = ""
    city: str
    region_code: str
    postal_code: str


async def set_cart_shipping_address(
    config: SetCartShippingAddressConfig,
    input_data: SetCartShippingAddressInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """把收货地址写到 cart.shipping_address(JSONB)。

    Args:
        config: SetCartShippingAddressConfig。
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
                "pool is required — set_cart_shipping_address always touches persisted cart"
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
            data={"shipping_address": json.dumps(address)},
        )
        trail.record(event="shipping_address_set", cart_id=input_data.cart_id)

        if on_step:
            on_step(
                {
                    "stage": "set_cart_shipping_address",
                    "status": "done",
                    "cart_id": input_data.cart_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            cart_id=input_data.cart_id,
            shipping_address=address,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
