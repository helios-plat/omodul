"""omodul.fulfill_swap — 执行换货:回补旧货库存 + new_items 预留转永久出库 +
建履约单据。

只能从 status='requested' 且差价已清(payment_status 为 paid 或
not_required)执行——先说明:差价未清就不能发货,防止"补差价没到账就
先把新货寄出去"。

Composes:
  - obase.persistence.transaction

Pillars: decision_trail
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_PAYABLE_STATUSES = {"paid", "not_required"}


class FulfillSwapConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "fulfill_swap"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class FulfillSwapInput(BaseModel):
    swap_id: str


async def fulfill_swap(
    config: FulfillSwapConfig,
    input_data: FulfillSwapInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """执行换货:回补旧货库存,new_items 预留转永久出库,建履约单据。

    Args:
        config: FulfillSwapConfig。
        input_data: swap_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 fulfillment_id。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — fulfill_swap always touches persisted swap")

        async with transaction(pool) as tx:
            swap = await tx.fetchrow(
                'SELECT * FROM "swap" WHERE id = $1 FOR UPDATE', input_data.swap_id
            )
            if swap is None:
                raise ValueError(f"swap {input_data.swap_id} not found")
            if swap["status"] != "requested":
                raise ValueError(
                    f"swap {input_data.swap_id} cannot be fulfilled from status {swap['status']!r}"
                )
            if swap["payment_status"] not in _PAYABLE_STATUSES:
                raise ValueError(
                    f"swap {input_data.swap_id} price difference not settled "
                    f"(payment_status={swap['payment_status']!r})"
                )

            return_items = json.loads(swap["return_items"])
            for item in return_items:
                line = await tx.fetchrow(
                    'SELECT * FROM "order_line_item" WHERE id = $1', item["order_line_item_id"]
                )
                if line is None:
                    raise ValueError(f"order_line_item {item['order_line_item_id']} not found")
                await tx.execute(
                    'UPDATE "inventory_batch" SET stock_qty = stock_qty + $1, '
                    "updated_at = NOW() WHERE id = $2",
                    item["quantity"],
                    line["batch_id"],
                )
            trail.record(event="old_items_restocked", items=len(return_items))

            new_items = json.loads(swap["new_items"])
            for item in new_items:
                await tx.execute(
                    'UPDATE "inventory_batch" SET stock_qty = stock_qty - $1, '
                    "reserved_qty = reserved_qty - $1, updated_at = NOW() WHERE id = $2",
                    item["quantity"],
                    item["batch_id"],
                )
            trail.record(event="new_items_converted_to_sale", items=len(new_items))

            fulfillment_id = uuid7()
            await tx.execute(
                'INSERT INTO "fulfillment" (id, order_id, items) VALUES ($1, $2, $3::jsonb)',
                fulfillment_id,
                swap["order_id"],
                json.dumps(new_items),
            )
            await tx.execute(
                "UPDATE \"swap\" SET status = 'fulfilled', fulfillment_id = $1, "
                "updated_at = NOW() WHERE id = $2",
                fulfillment_id,
                input_data.swap_id,
            )
            trail.record(
                event="swap_fulfilled",
                swap_id=input_data.swap_id,
                fulfillment_id=str(fulfillment_id),
            )

        if on_step:
            on_step({"stage": "fulfill_swap", "status": "done", "swap_id": input_data.swap_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            swap_id=input_data.swap_id,
            fulfillment_id=fulfillment_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
