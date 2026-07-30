"""omodul.apply_discount_to_cart — 把折扣码生效到购物车,分摊到适用行,重算 totals。

Composes:
  - obase.cache.DistributedLock(按折扣 code 加锁 —— 并发风险来自多个购物车争抢
    同一张有用量上限的折扣码,不是同一购物车内部并发)
  - obase.persistence.transaction(资格判定 + 条件过滤 + 分摊计算 + 用量自增 +
    cart totals 写回,同一事务内原子提交;discount_rule 行锁作为第二道防线)
  - oskill.evaluate_discount_eligibility / evaluate_discount_conditions /
    apply_discount_amount / apply_discount_percentage / compute_cart_grand_total

Pillars: fingerprint + decision_trail

设计要点(自由裁量,SPEC 未给出细节):
  - free_shipping 类型折扣不写回 cart.shipping_cents(那样会销毁原始运费、
    remove 时无法恢复);而是在每次重算 grand_total 时,查是否存在生效中的
    free_shipping 折扣,若有则临时把 shipping 传 0 给
    oskill.compute_cart_grand_total,cart.shipping_cents 本身保持不变。
  - cart.discount_cents 只累加非 free_shipping 折扣的 applied_amount_cents;
    free_shipping 折扣的 applied_amount_cents 只作审计记录(“本次折扣省了多少
    运费”),不计入 discount_cents 这个字段。
  - discount_condition 池:多行条目视为同一 condition_type 的并集(取首行
    type,收集全部 target_id);任一行 type='all' 视为整单不限白名单。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: ApplyDiscountToCartConfig, input_data: ApplyDiscountToCartInput
) -> str:
    """Fingerprint over cart_id + code。"""
    return compute_fingerprint({"cart_id": input_data.cart_id, "code": input_data.code})


class ApplyDiscountToCartConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "apply_discount_to_cart"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"cart_id", "code"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    redis_url: str = "redis://localhost:6379/0"
    lock_timeout_seconds: float = 5.0


class ApplyDiscountToCartInput(BaseModel):
    cart_id: str
    code: str


def _row_to_rule_dict(rule_row: dict) -> dict:
    rule: dict[str, Any] = {}
    if rule_row["min_subtotal_cents"] is not None:
        rule["min_subtotal_cents"] = rule_row["min_subtotal_cents"]
    if rule_row["valid_from"] is not None:
        rule["valid_from"] = rule_row["valid_from"].isoformat()
    if rule_row["valid_until"] is not None:
        rule["valid_until"] = rule_row["valid_until"].isoformat()
    if rule_row["region_codes"]:
        rule["region_codes"] = list(rule_row["region_codes"])
    if rule_row["max_uses"] is not None:
        rule["max_uses"] = rule_row["max_uses"]
        rule["uses_count"] = rule_row["uses_count"]
    return rule


async def apply_discount_to_cart(
    config: ApplyDiscountToCartConfig,
    input_data: ApplyDiscountToCartInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """按折扣码把折扣生效到购物车。

    Args:
        config: ApplyDiscountToCartConfig。
        input_data: cart_id / code。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 discount_id / applied_amount_cents / cart totals。
    """
    from obase.cache import DistributedLock
    from obase.exceptions import LockAcquisitionError
    from obase.persistence import transaction
    from obase.uuid7 import uuid7
    from oskill import (
        apply_discount_amount,
        apply_discount_percentage,
        compute_cart_grand_total,
        evaluate_discount_conditions,
        evaluate_discount_eligibility,
    )

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — apply_discount_to_cart always touches persisted stock"
            )

        fp = compute_fingerprint_for(config, input_data)

        async with DistributedLock(
            key=f"discount:{input_data.code}",
            redis_url=config.redis_url,
            timeout_seconds=config.lock_timeout_seconds,
        ):
            trail.record(event="lock_acquired", code=input_data.code)

            async with transaction(pool) as tx:
                discount = await tx.fetchrow(
                    'SELECT * FROM "discount" WHERE code = $1 AND deleted_at IS NULL',
                    input_data.code,
                )
                if discount is None:
                    raise ValueError(f"discount code {input_data.code!r} not found")
                if discount["status"] != "active":
                    raise ValueError(f"discount code {input_data.code!r} is not active")

                rule = await tx.fetchrow(
                    'SELECT * FROM "discount_rule" WHERE discount_id = $1 FOR UPDATE',
                    discount["id"],
                )
                if rule is None:
                    raise ValueError(f"discount {discount['id']} has no rule attached")

                cart = await tx.fetchrow(
                    'SELECT * FROM "cart" WHERE id = $1 AND deleted_at IS NULL FOR UPDATE',
                    input_data.cart_id,
                )
                if cart is None:
                    raise ValueError(f"cart {input_data.cart_id} not found")

                existing_application = await tx.fetchrow(
                    'SELECT 1 FROM "cart_discount" WHERE cart_id = $1 AND discount_id = $2 '
                    "AND deleted_at IS NULL",
                    input_data.cart_id,
                    discount["id"],
                )
                if existing_application is not None:
                    raise ValueError(
                        f"discount {input_data.code!r} is already applied to this cart"
                    )

                cart_dict = {
                    "subtotal_cents": cart["subtotal_cents"],
                    "region_code": cart["region_code"],
                }
                if not evaluate_discount_eligibility(cart_dict, rule=_row_to_rule_dict(rule)):
                    raise ValueError(
                        f"cart {input_data.cart_id} is not eligible for discount "
                        f"{input_data.code!r}"
                    )

                condition_rows = await tx.fetch(
                    'SELECT * FROM "discount_condition" WHERE discount_id = $1', discount["id"]
                )
                if not condition_rows or any(r["condition_type"] == "all" for r in condition_rows):
                    condition = {"type": "all"}
                else:
                    condition = {
                        "type": condition_rows[0]["condition_type"] + "s",
                        "target_ids": [str(r["target_id"]) for r in condition_rows],
                    }

                line_rows = await tx.fetch(
                    """
                    SELECT cli.id, cli.line_total_cents, pv.product_id, p.category_id
                    FROM "cart_line_item" cli
                    JOIN "inventory_batch" ib ON ib.id = cli.batch_id
                    JOIN "product_variant" pv ON pv.id = ib.variant_id
                    JOIN "product" p ON p.id = pv.product_id
                    WHERE cli.cart_id = $1 AND cli.deleted_at IS NULL
                    """,
                    input_data.cart_id,
                )
                items = [
                    {
                        "id": str(r["id"]),
                        "line_total_cents": r["line_total_cents"],
                        "product_id": str(r["product_id"]),
                        "category_id": str(r["category_id"]) if r["category_id"] else None,
                    }
                    for r in line_rows
                ]
                eligible_items = evaluate_discount_conditions(items, condition=condition)

                rule_type = rule["rule_type"]
                if rule_type == "fixed":
                    allocation = apply_discount_amount(eligible_items, amount=rule["amount_cents"])
                    applied_amount_cents = allocation["total_discount_cents"]
                elif rule_type == "percentage":
                    allocation = apply_discount_percentage(eligible_items, percent=rule["percent"])
                    applied_amount_cents = allocation["total_discount_cents"]
                else:  # free_shipping
                    applied_amount_cents = cart["shipping_cents"]

                cart_discount_id = uuid7()
                await tx.execute(
                    'INSERT INTO "cart_discount" '
                    "(id, cart_id, discount_id, applied_amount_cents) VALUES ($1, $2, $3, $4)",
                    cart_discount_id,
                    input_data.cart_id,
                    discount["id"],
                    applied_amount_cents,
                )
                await tx.execute(
                    'UPDATE "discount_rule" SET uses_count = uses_count + 1 WHERE id = $1',
                    rule["id"],
                )
                trail.record(
                    event="discount_applied",
                    cart_discount_id=str(cart_discount_id),
                    rule_type=rule_type,
                    applied_amount_cents=applied_amount_cents,
                )

                discount_cents = await tx.fetchval(
                    """
                    SELECT COALESCE(SUM(cd.applied_amount_cents), 0)
                    FROM "cart_discount" cd
                    JOIN "discount_rule" dr ON dr.discount_id = cd.discount_id
                    WHERE cd.cart_id = $1 AND cd.deleted_at IS NULL
                    AND dr.rule_type != 'free_shipping'
                    """,
                    input_data.cart_id,
                )
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
                effective_shipping = 0 if has_free_shipping else cart["shipping_cents"]
                grand_total = compute_cart_grand_total(
                    cart["subtotal_cents"],
                    discount=discount_cents,
                    tax=cart["tax_cents"],
                    shipping=effective_shipping,
                )
                await tx.execute(
                    'UPDATE "cart" SET discount_cents = $1, grand_total_cents = $2, '
                    "updated_at = NOW() WHERE id = $3",
                    discount_cents,
                    grand_total,
                    input_data.cart_id,
                )
                trail.record(
                    event="cart_totals_recomputed",
                    discount_cents=discount_cents,
                    grand_total=grand_total,
                )

        if on_step:
            on_step(
                {
                    "stage": "apply_discount_to_cart",
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
            discount_id=discount["id"],
            applied_amount_cents=applied_amount_cents,
            discount_cents=discount_cents,
            grand_total_cents=grand_total,
        )

    except LockAcquisitionError as exc:
        trail.record(event="lock_timeout", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": "LockAcquisitionError", "message": str(exc)},
        )
    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
