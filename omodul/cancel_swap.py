"""omodul.cancel_swap — 取消尚未履约的换货,释放 new_items 的预留库存。

Pillars: decision_trail
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CancelSwapConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "cancel_swap"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CancelSwapInput(BaseModel):
    swap_id: str


async def cancel_swap(
    config: CancelSwapConfig,
    input_data: CancelSwapInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """取消一笔尚未履约的换货,释放 new_items 的预留库存。

    Args:
        config: CancelSwapConfig。
        input_data: swap_id。
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
            raise ValueError("pool is required — cancel_swap always touches persisted swap")

        async with transaction(pool) as tx:
            swap = await tx.fetchrow(
                'SELECT * FROM "swap" WHERE id = $1 FOR UPDATE', input_data.swap_id
            )
            if swap is None:
                raise ValueError(f"swap {input_data.swap_id} not found")
            if swap["status"] != "requested":
                raise ValueError(
                    f"swap {input_data.swap_id} cannot be canceled from status {swap['status']!r}"
                )

            for item in json.loads(swap["new_items"]):
                await tx.execute(
                    'UPDATE "inventory_batch" SET reserved_qty = reserved_qty - $1, '
                    "updated_at = NOW() WHERE id = $2",
                    item["quantity"],
                    item["batch_id"],
                )
            trail.record(event="new_items_released")

            await tx.execute(
                "UPDATE \"swap\" SET status = 'canceled', updated_at = NOW() WHERE id = $1",
                input_data.swap_id,
            )
            trail.record(event="swap_canceled", swap_id=input_data.swap_id)

        if on_step:
            on_step({"stage": "cancel_swap", "status": "done", "swap_id": input_data.swap_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            swap_id=input_data.swap_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
