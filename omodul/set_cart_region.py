"""omodul.set_cart_region — 切换购物车所属区域/币种。

Composes:
  - obase.persistence.read_one / update_one（cart 存在性校验 + 写回）

范围声明（本轮显式排除，对齐已确认的 vertical slice 范围）：
  - 不做"跨区不兼容项清空"：本轮无区域级 SKU/运费白名单规则，行不因换区被清空。
  - 不重算 totals：换区不改变已在车的 batch 单价（批次价格与区域无关），无需触发
    oskill.compute_cart_grand_total。

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(config: SetCartRegionConfig, input_data: SetCartRegionInput) -> str:
    """Fingerprint over cart_id + region_code。"""
    return compute_fingerprint(
        {
            "cart_id": input_data.cart_id,
            "region_code": input_data.region_code,
        }
    )


class SetCartRegionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "set_cart_region"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "region_code"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class SetCartRegionInput(BaseModel):
    cart_id: str
    region_code: str
    currency: str


async def set_cart_region(
    config: SetCartRegionConfig,
    input_data: SetCartRegionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """把购物车切到指定区域/币种（对齐 oskill.select_region_currency 的调用方约定）。

    Args:
        config: SetCartRegionConfig。
        input_data: cart_id / region_code / currency。
        output_dir: decision_trail 落盘目录（本元素未启用 decision_trail pillar）。
        pool: obase.persistence.PgPool。本函数必须落库，pool 为 None 直接判 failed。
        on_step: 进度回调，可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if not input_data.region_code:
            raise ValueError("region_code is required")
        if not input_data.currency:
            raise ValueError("currency is required")
        if pool is None:
            raise ValueError("pool is required — set_cart_region always touches persisted cart")

        fp = compute_fingerprint_for(config, input_data)

        cart = await read_one(pool, table="cart", id=input_data.cart_id)
        if cart is None:
            raise ValueError(f"cart {input_data.cart_id} not found")

        await update_one(
            pool,
            table="cart",
            id=input_data.cart_id,
            data={"region_code": input_data.region_code, "currency": input_data.currency},
        )
        trail.record(
            event="region_switched",
            cart_id=input_data.cart_id,
            region_code=input_data.region_code,
            currency=input_data.currency,
        )

        if on_step:
            on_step({"stage": "set_cart_region", "status": "done", "cart_id": input_data.cart_id})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            cart_id=input_data.cart_id,
            region_code=input_data.region_code,
            currency=input_data.currency,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
