"""omodul.create_cart — 购物车容器初始化。

Composes:
  - obase.uuid7.uuid7 (cart id)
  - obase.persistence.insert_one (写入 cart 表)

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(config: CreateCartConfig, input_data: CreateCartInput) -> str:
    """Fingerprint over customer_id + region_code —— 同一用户同区域重复建车可被识别为幂等重试。"""
    return compute_fingerprint(
        {
            "customer_id": input_data.customer_id,
            "region_code": input_data.region_code,
        }
    )


class CreateCartConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_cart"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"customer_id", "region_code"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}

    default_currency: str = "CNY"


class CreateCartInput(BaseModel):
    customer_id: str = ""
    region_code: str = ""
    currency: str = ""


async def create_cart(
    config: CreateCartConfig,
    input_data: CreateCartInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建一个空购物车容器（customer_id/region_code 可选，匿名车允许两者皆空）。

    Args:
        config: CreateCartConfig。
        input_data: customer_id / region_code / currency，均可选。
        output_dir: decision_trail 落盘目录（本元素未启用 decision_trail pillar，传入亦忽略）。
        pool: obase.persistence.PgPool；为 None 时跳过持久化（dry-run，便于单测）。
        on_step: 进度回调，可选。

    Returns:
        标准 omodul 返回 dict，findings 中含 cart_id。
    """
    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        fp = compute_fingerprint_for(config, input_data)

        cart_id = uuid7()
        row = {
            "id": cart_id,
            "customer_id": input_data.customer_id or None,
            "region_code": input_data.region_code or None,
            "currency": input_data.currency or config.default_currency,
        }

        if pool is not None:
            await insert_one(pool, table="cart", data=row)
            trail.record(event="persisted", cart_id=cart_id)
        else:
            trail.record(event="persisted_skipped_no_pool", cart_id=cart_id)

        if on_step:
            on_step({"stage": "create_cart", "status": "done", "cart_id": cart_id})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            cart_id=cart_id,
            customer_id=row["customer_id"],
            region_code=row["region_code"],
            currency=row["currency"],
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
