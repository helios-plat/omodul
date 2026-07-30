"""omodul.create_draft_order — 代客下单,不经过购物车/支付会话流程。

customer_order.cart_id 为 NULL 就是给这条路径用的(Postgres UNIQUE 约束
下多个 NULL 不冲突)。范围声明(自由裁量,SPEC 未给细节,过薄不做):
  - 不支持折扣/税费/运费——discount_cents/tax_cents/shipping_cents 恒为 0。
    真实场景里代客下单也可能要打折/收运费,但那需要把折扣引擎、地址路由
    等一整套逻辑在"无 cart"路径下重新接一遍,超出本轮范围。
  - 行项目在创建时一次性给全,后续要改用 update_draft_order(只改
    地址/客户,不改行)或删了重建,不支持增量加/删行。

Composes:
  - obase.persistence.transaction(逐行校验库存 + 预留 + 建单)
  - oskill.compute_cart_subtotal / compute_cart_grand_total

Pillars: report, decision_trail(SPEC 未给 fingerprint,遵照原样)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class DraftOrderLineItem(BaseModel):
    batch_id: str
    quantity: int


class CreateDraftOrderConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_draft_order"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}

    default_currency: str = "CNY"


class CreateDraftOrderInput(BaseModel):
    customer_id: str = ""
    region_code: str = ""
    currency: str = ""
    line_items: list[DraftOrderLineItem]
    billing_address: dict[str, Any] | None = None
    shipping_address: dict[str, Any] | None = None


async def create_draft_order(
    config: CreateDraftOrderConfig,
    input_data: CreateDraftOrderInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """按给定行项目直接建一张草稿订单,预留对应库存。

    Args:
        config: CreateDraftOrderConfig。
        input_data: customer_id/region_code/currency/line_items/地址。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 order_id / totals。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7
    from oskill import compute_cart_grand_total, compute_cart_subtotal

    trail = Trail()

    try:
        if not input_data.line_items:
            raise ValueError("line_items must not be empty")
        if pool is None:
            raise ValueError("pool is required — create_draft_order always touches persisted stock")

        async with transaction(pool) as tx:
            line_rows: list[dict] = []
            for item in input_data.line_items:
                batch = await tx.fetchrow(
                    'SELECT * FROM "inventory_batch" WHERE id = $1 FOR UPDATE', item.batch_id
                )
                if batch is None:
                    raise ValueError(f"batch {item.batch_id} not found")
                if batch["status"] != "active" or batch["inspection_status"] != "passed":
                    raise ValueError(f"batch {item.batch_id} is not sellable")
                available = batch["stock_qty"] - batch["reserved_qty"]
                if available < item.quantity:
                    raise ValueError(
                        f"insufficient stock for batch {item.batch_id}: "
                        f"requested {item.quantity}, available {available}"
                    )

                await tx.execute(
                    'UPDATE "inventory_batch" SET reserved_qty = reserved_qty + $1, '
                    "updated_at = NOW() WHERE id = $2",
                    item.quantity,
                    item.batch_id,
                )
                unit_price_cents = batch["retail_price_cents"]
                line_rows.append(
                    {
                        "batch_id": item.batch_id,
                        "quantity": item.quantity,
                        "unit_price_cents": unit_price_cents,
                        "line_total_cents": unit_price_cents * item.quantity,
                    }
                )

            subtotal = compute_cart_subtotal(line_rows)
            grand_total = compute_cart_grand_total(subtotal, discount=0, tax=0, shipping=0)

            order_id = uuid7()
            await tx.execute(
                'INSERT INTO "customer_order" '
                "(id, cart_id, customer_id, region_code, currency, status, subtotal_cents, "
                "discount_cents, tax_cents, shipping_cents, grand_total_cents, "
                "billing_address, shipping_address) "
                "VALUES "
                "($1, NULL, $2, $3, $4, 'draft', $5, 0, 0, 0, $6, $7, $8)",
                order_id,
                input_data.customer_id or None,
                input_data.region_code or None,
                input_data.currency or config.default_currency,
                subtotal,
                grand_total,
                json.dumps(input_data.billing_address) if input_data.billing_address else None,
                json.dumps(input_data.shipping_address) if input_data.shipping_address else None,
            )
            for line in line_rows:
                await tx.execute(
                    'INSERT INTO "order_line_item" '
                    "(id, order_id, batch_id, quantity, unit_price_cents, line_total_cents) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    uuid7(),
                    order_id,
                    line["batch_id"],
                    line["quantity"],
                    line["unit_price_cents"],
                    line["line_total_cents"],
                )
            trail.record(event="draft_order_created", order_id=str(order_id), lines=len(line_rows))

        if on_step:
            on_step({"stage": "create_draft_order", "status": "done", "order_id": order_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            order_id=order_id,
            subtotal_cents=subtotal,
            grand_total_cents=grand_total,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
