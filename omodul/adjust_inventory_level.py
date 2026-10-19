"""omodul.adjust_inventory_level — 盘点盈亏,强审计。

对齐 SPEC "盘点盈亏 + 强审计日志"：本仓库的批次模型下,库存主体是
inventory_batch.stock_qty,不是通用 inventory_item——盘点调整直接作用在
批次上。delta 可正可负(盘盈/盘亏),reason 必填(强审计要求留痕原因,
不接受"无理由调整库存")。拒绝把 stock_qty 调到低于 reserved_qty(会让
已经加购/下单的数量凭空超卖)或调到负数。

Composes:
  - obase.persistence.transaction(行锁 + 校验 + 调整同一事务)

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class AdjustInventoryLevelConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "adjust_inventory_level"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class AdjustInventoryLevelInput(BaseModel):
    batch_id: str
    delta: int  # 正数盘盈,负数盘亏
    reason: str


async def adjust_inventory_level(
    config: AdjustInventoryLevelConfig,
    input_data: AdjustInventoryLevelInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """按 delta 调整批次库存,reason 必填,强审计记录调整前后值。

    Args:
        config: AdjustInventoryLevelConfig。
        input_data: batch_id / delta(正盈负亏)/ reason。
        output_dir: decision_trail 落盘目录 —— 盘点调整是高审计动作,必须留痕。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含调整前后的 stock_qty。
    """
    from obase.persistence import transaction

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — adjust_inventory_level always touches persisted batch"
            )
        if input_data.delta == 0:
            raise ValueError("delta must be non-zero")
        if not input_data.reason:
            raise ValueError("reason is required for an inventory adjustment")

        async with transaction(pool) as tx:
            batch = await tx.fetchrow(
                'SELECT * FROM "inventory_batch" WHERE id = $1 FOR UPDATE', input_data.batch_id
            )
            if batch is None:
                raise ValueError(f"batch {input_data.batch_id} not found")

            old_stock_qty = batch["stock_qty"]
            new_stock_qty = old_stock_qty + input_data.delta
            if new_stock_qty < 0:
                raise ValueError(
                    f"adjustment would make stock_qty negative "
                    f"({old_stock_qty} + {input_data.delta} = {new_stock_qty})"
                )
            if new_stock_qty < batch["reserved_qty"]:
                raise ValueError(
                    f"adjustment would drop stock_qty ({new_stock_qty}) below "
                    f"reserved_qty ({batch['reserved_qty']}) — would create an oversell"
                )

            await tx.execute(
                'UPDATE "inventory_batch" SET stock_qty = $1, updated_at = NOW() WHERE id = $2',
                new_stock_qty,
                input_data.batch_id,
            )
            trail.record(
                event="inventory_adjusted",
                batch_id=input_data.batch_id,
                delta=input_data.delta,
                reason=input_data.reason,
                old_stock_qty=old_stock_qty,
                new_stock_qty=new_stock_qty,
            )

        if on_step:
            on_step(
                {
                    "stage": "adjust_inventory_level",
                    "status": "done",
                    "batch_id": input_data.batch_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            batch_id=input_data.batch_id,
            old_stock_qty=old_stock_qty,
            new_stock_qty=new_stock_qty,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
