"""omodul.delete_draft_order — 硬删草稿订单,释放预留库存。

只能对 status='draft' 的订单生效——已经 mark_draft_order_paid 转正的订单
要用 cancel_order(涉及退款联动),不是这里。硬删(不是软删/归档)是因为
草稿单从没真的成交过,没有财务记录需要保留。

Composes:
  - obase.persistence.transaction(逐行释放预留 + 删行 + 删单)

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class DeleteDraftOrderConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_draft_order"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class DeleteDraftOrderInput(BaseModel):
    order_id: str


async def delete_draft_order(
    config: DeleteDraftOrderConfig,
    input_data: DeleteDraftOrderInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """硬删草稿订单,释放它占用的 reserved_qty。

    Args:
        config: DeleteDraftOrderConfig。
        input_data: order_id。
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
            raise ValueError("pool is required — delete_draft_order always touches persisted order")

        async with transaction(pool) as tx:
            order = await tx.fetchrow(
                'SELECT * FROM "customer_order" WHERE id = $1 FOR UPDATE', input_data.order_id
            )
            if order is None:
                raise ValueError(f"order {input_data.order_id} not found")
            if order["status"] != "draft":
                raise ValueError(
                    f"order {input_data.order_id} is not a draft (status={order['status']!r}) "
                    "— use cancel_order for placed orders"
                )

            lines = await tx.fetch(
                'SELECT * FROM "order_line_item" WHERE order_id = $1', input_data.order_id
            )
            for line in lines:
                await tx.execute(
                    'UPDATE "inventory_batch" SET reserved_qty = reserved_qty - $1, '
                    "updated_at = NOW() WHERE id = $2",
                    line["quantity"],
                    line["batch_id"],
                )
            trail.record(event="reservations_released", lines=len(lines))

            await tx.execute(
                'DELETE FROM "order_line_item" WHERE order_id = $1', input_data.order_id
            )
            await tx.execute('DELETE FROM "customer_order" WHERE id = $1', input_data.order_id)
            trail.record(event="draft_order_deleted", order_id=input_data.order_id)

        if on_step:
            on_step(
                {"stage": "delete_draft_order", "status": "done", "order_id": input_data.order_id}
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
