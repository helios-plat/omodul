"""omodul.set_cart_customer — 把购物车绑定到具体买家。

Composes:
  - obase.persistence.read_one / update_one（cart 存在性校验 + 写回）

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: SetCartCustomerConfig, input_data: SetCartCustomerInput
) -> str:
    """Fingerprint over cart_id + customer_id。"""
    return compute_fingerprint(
        {
            "cart_id": input_data.cart_id,
            "customer_id": input_data.customer_id,
        }
    )


class SetCartCustomerConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "set_cart_customer"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "customer_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class SetCartCustomerInput(BaseModel):
    cart_id: str
    customer_id: str


async def set_cart_customer(
    config: SetCartCustomerConfig,
    input_data: SetCartCustomerInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """把匿名购物车绑定到 customer_id（例如登录后合并购物车归属）。

    Args:
        config: SetCartCustomerConfig。
        input_data: cart_id / customer_id。
        output_dir: decision_trail 落盘目录（本元素未启用 decision_trail pillar）。
        pool: obase.persistence.PgPool。本函数必须落库，pool 为 None 直接判 failed。
        on_step: 进度回调，可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if not input_data.customer_id:
            raise ValueError("customer_id is required")
        if pool is None:
            raise ValueError("pool is required — set_cart_customer always touches persisted cart")

        fp = compute_fingerprint_for(config, input_data)

        cart = await read_one(pool, table="cart", id=input_data.cart_id)
        if cart is None:
            raise ValueError(f"cart {input_data.cart_id} not found")

        await update_one(
            pool,
            table="cart",
            id=input_data.cart_id,
            data={"customer_id": input_data.customer_id},
        )
        trail.record(
            event="customer_bound",
            cart_id=input_data.cart_id,
            customer_id=input_data.customer_id,
        )

        if on_step:
            on_step({"stage": "set_cart_customer", "status": "done", "cart_id": input_data.cart_id})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            cart_id=input_data.cart_id,
            customer_id=input_data.customer_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
