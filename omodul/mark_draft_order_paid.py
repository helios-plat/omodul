"""omodul.mark_draft_order_paid — 把草稿订单转正为已付款订单。

不调 provider.authorize()/capture()——"mark paid"这个名字本身说的是"钱已经
在系统之外收好了"(线下现金/POS/银行转账),本函数只是把这个既成事实记进
系统:预留转永久出库(跟 complete_checkout 同一个"reservation → sale"转换),
status 从 draft 推进到 pending,payment_provider_name/payment_intent_id
按调用方传入的值落库(可以是 "manual" + 一个内部流水号,也可以是真实
provider 那边已经完成的 intent_id——本函数不关心具体是哪种,只负责记录)。

Composes:
  - obase.persistence.transaction

Pillars: report, decision_trail(SPEC 未给 fingerprint,遵照原样)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class MarkDraftOrderPaidConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "mark_draft_order_paid"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class MarkDraftOrderPaidInput(BaseModel):
    order_id: str
    payment_provider_name: str = "manual"
    payment_intent_id: str = ""


async def mark_draft_order_paid(
    config: MarkDraftOrderPaidConfig,
    input_data: MarkDraftOrderPaidInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """把草稿订单转正:预留转永久出库,status → pending。

    Args:
        config: MarkDraftOrderPaidConfig。
        input_data: order_id / payment_provider_name / payment_intent_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import transaction

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — mark_draft_order_paid always touches persisted order"
            )

        async with transaction(pool) as tx:
            order = await tx.fetchrow(
                'SELECT * FROM "customer_order" WHERE id = $1 FOR UPDATE', input_data.order_id
            )
            if order is None:
                raise ValueError(f"order {input_data.order_id} not found")
            if order["status"] != "draft":
                raise ValueError(
                    f"order {input_data.order_id} is not a draft (status={order['status']!r})"
                )

            lines = await tx.fetch(
                'SELECT * FROM "order_line_item" WHERE order_id = $1', input_data.order_id
            )
            for line in lines:
                await tx.execute(
                    'UPDATE "inventory_batch" SET stock_qty = stock_qty - $1, '
                    "reserved_qty = reserved_qty - $1, updated_at = NOW() WHERE id = $2",
                    line["quantity"],
                    line["batch_id"],
                )
            trail.record(event="inventory_converted_to_sale", lines=len(lines))

            await tx.execute(
                "UPDATE \"customer_order\" SET status = 'pending', payment_provider_name = $1, "
                "payment_intent_id = $2, updated_at = NOW() WHERE id = $3",
                input_data.payment_provider_name,
                input_data.payment_intent_id or None,
                input_data.order_id,
            )
            trail.record(
                event="draft_order_marked_paid",
                order_id=input_data.order_id,
                payment_provider_name=input_data.payment_provider_name,
            )

        if on_step:
            on_step(
                {
                    "stage": "mark_draft_order_paid",
                    "status": "done",
                    "order_id": input_data.order_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            order_id=input_data.order_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
