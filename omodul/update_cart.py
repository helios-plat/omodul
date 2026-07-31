"""omodul.update_cart — 购物车容器的通用原始字段更新。

SPEC §4.6 的 `create_cart / update_cart | fingerprint | 容器初始化` 一对：
create_cart 初始化容器，update_cart 对通用购物车字段做部分原始写入
（partial update——只更新 input_data 里实际给出的字段，SET 子句动态拼装）。

范围声明（显式不与专用 setter 重叠）：
  - 不重算 totals：不碰 subtotal/discount/tax/shipping/grand_total 任何一列，
    总额重算是 oskill.compute_cart_grand_total 的职责。
  - 不清空不兼容行项：不碰 cart_line_item。
  - 不触发生命周期副作用：'payment_authorized' 由 authorize_payment_for_cart
    推进、'completed' 由 complete_checkout 推进；这里可写的 status 仅限
    手工/管理类转移（active / abandoned / expired），写别的状态直接判 failed。
  - region_code + currency 联动（含重算/清空不兼容项）归 set_cart_region，
    customer_id 绑定归 set_cart_customer，地址归 set_cart_billing_address /
    set_cart_shipping_address；本元素对 status / customer_id / currency 只做
    原始写入，不附带上述 setter 的任何副作用。

Composes:
  - obase.persistence.transaction（对 cart 表走原始 SQL 写回）

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

# 允许原始写入的标量字段白名单（cart 表列）；其余一律由专用 setter 负责。
_UPDATABLE_FIELDS = ("status", "customer_id", "currency")


def compute_fingerprint_for(config: UpdateCartConfig, input_data: UpdateCartInput) -> str:
    """Fingerprint over cart_id。"""
    return compute_fingerprint({"cart_id": input_data.cart_id})


class UpdateCartConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_cart"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class UpdateCartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 未知字段在构造期即拒绝

    cart_id: str
    status: str | None = None  # 'active' | 'abandoned' | 'expired'
    customer_id: str | None = None
    currency: str | None = None


async def update_cart(
    config: UpdateCartConfig,
    input_data: UpdateCartInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """对购物车容器做通用原始字段更新（部分更新，只写给出的字段）。

    原始写入语义：不重算 totals、不清空不兼容行项、不触发生命周期——
    那些副作用分别是 oskill.compute_cart_grand_total、set_cart_region、
    authorize_payment_for_cart / complete_checkout 的职责。

    Args:
        config: UpdateCartConfig。
        input_data: cart_id + 可选 status / customer_id / currency（至少给一个）。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库，pool 为 None 直接判 failed。
        on_step: 进度回调，可选。

    Returns:
        标准 omodul 返回 dict；findings 含 cart_id / updated_fields 及所写字段值
        （status 以 status_value 回传，避免与返回结构自身的 status 键冲突）。
    """
    from obase.persistence import transaction

    trail = Trail()

    try:
        updates: dict[str, Any] = {}
        for name in _UPDATABLE_FIELDS:
            value = getattr(input_data, name)
            if name in input_data.model_fields_set and value is not None:
                updates[name] = value

        if "status" in updates and updates["status"] not in ("active", "abandoned", "expired"):
            raise ValueError(
                f"invalid status: {updates['status']!r} — allowed: active / abandoned / expired"
            )
        if pool is None:
            raise ValueError("pool is required — update_cart always touches persisted cart")
        if not updates:
            raise ValueError(
                "no updatable fields provided — pass at least one of "
                "status / customer_id / currency"
            )

        fp = compute_fingerprint_for(config, input_data)

        async with transaction(pool) as tx:
            cart = await tx.fetchrow(
                'SELECT * FROM "cart" WHERE id = $1 AND deleted_at IS NULL FOR UPDATE',
                input_data.cart_id,
            )
            if cart is None:
                raise ValueError(f"cart {input_data.cart_id} not found")

            set_parts = []
            params: list[Any] = []
            for col, val in updates.items():
                params.append(val)
                set_parts.append(f'"{col}" = ${len(params)}')
            params.append(input_data.cart_id)
            await tx.execute(
                'UPDATE "cart" SET '
                + ", ".join(set_parts)
                + f", updated_at = NOW() WHERE id = ${len(params)}",
                *params,
            )
        trail.record(
            event="cart_updated",
            cart_id=input_data.cart_id,
            fields=sorted(updates),
        )

        if on_step:
            on_step({"stage": "update_cart", "status": "done", "cart_id": input_data.cart_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        findings: dict[str, Any] = {k: v for k, v in updates.items() if k != "status"}
        if "status" in updates:
            findings["status_value"] = updates["status"]

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            cart_id=input_data.cart_id,
            updated_fields=sorted(updates),
            **findings,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
