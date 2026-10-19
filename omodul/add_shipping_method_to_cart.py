"""omodul.add_shipping_method_to_cart — 给购物车计入运费,重算 grand_total。

范围声明(自由裁量,SPEC 未给出细节):没有独立的 shipping_method 表——
cart 本身只有一个 shipping_cents 字段(不支持"多种运费方式并存,用户选一个"
的列表语义),所以本元素语义其实是"设置"而不是"追加",method_name 只作
审计记录,不落库(cart 表没有对应列)。真实运费方案计算(承运商报价 API)
不在本元素职责内,price_cents 由调用方传入。

Composes:
  - obase.persistence.transaction(cart 行锁 + 写回 + totals 重算)
  - oskill.compute_cart_grand_total

Pillars: fingerprint + decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: AddShippingMethodToCartConfig, input_data: AddShippingMethodToCartInput
) -> str:
    """Fingerprint over cart_id + method_name + price_cents。"""
    return compute_fingerprint(
        {
            "cart_id": input_data.cart_id,
            "method_name": input_data.method_name,
            "price_cents": input_data.price_cents,
        }
    )


class AddShippingMethodToCartConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "add_shipping_method_to_cart"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "method_name", "price_cents"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}


class AddShippingMethodToCartInput(BaseModel):
    cart_id: str
    method_name: str
    price_cents: int


async def add_shipping_method_to_cart(
    config: AddShippingMethodToCartConfig,
    input_data: AddShippingMethodToCartInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """把运费计入购物车,重算 grand_total(如已有生效中的 free_shipping 折扣,
    effective shipping 仍算 0,不会因为后设置运费而覆盖掉折扣的效果)。

    Args:
        config: AddShippingMethodToCartConfig。
        input_data: cart_id / method_name / price_cents。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 cart totals。
    """
    from obase.persistence import transaction
    from oskill import compute_cart_grand_total

    trail = Trail()

    try:
        if input_data.price_cents < 0:
            raise ValueError("price_cents must be non-negative")
        if pool is None:
            raise ValueError(
                "pool is required — add_shipping_method_to_cart always touches persisted cart"
            )

        fp = compute_fingerprint_for(config, input_data)

        async with transaction(pool) as tx:
            cart = await tx.fetchrow(
                'SELECT * FROM "cart" WHERE id = $1 AND deleted_at IS NULL FOR UPDATE',
                input_data.cart_id,
            )
            if cart is None:
                raise ValueError(f"cart {input_data.cart_id} not found")

            has_free_shipping = await tx.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1 FROM "cart_discount" cd
                    JOIN "discount_rule" dr ON dr.discount_id = cd.discount_id
                    WHERE cd.cart_id = $1 AND cd.deleted_at IS NULL
                    AND dr.rule_type = 'free_shipping'
                )
                """,
                input_data.cart_id,
            )
            effective_shipping = 0 if has_free_shipping else input_data.price_cents

            grand_total = compute_cart_grand_total(
                cart["subtotal_cents"],
                discount=cart["discount_cents"],
                tax=cart["tax_cents"],
                shipping=effective_shipping,
            )
            await tx.execute(
                'UPDATE "cart" SET shipping_cents = $1, grand_total_cents = $2, '
                "updated_at = NOW() WHERE id = $3",
                input_data.price_cents,
                grand_total,
                input_data.cart_id,
            )
            trail.record(
                event="shipping_method_added",
                method_name=input_data.method_name,
                price_cents=input_data.price_cents,
                effective_shipping=effective_shipping,
                grand_total=grand_total,
            )

        if on_step:
            on_step(
                {
                    "stage": "add_shipping_method_to_cart",
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
            shipping_cents=input_data.price_cents,
            grand_total_cents=grand_total,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
