"""omodul.create_inventory_batch — 自营质检入库并挂载视频，生成批次。

Composes:
  - obase.uuid7.uuid7 (batch id)
  - obase.persistence.insert_one (写入 inventory_batch 表)

Pillars: fingerprint + decision_trail
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: CreateInventoryBatchConfig, input_data: CreateInventoryBatchInput
) -> str:
    """Fingerprint over batch_no + variant_id + location_id.

    同一批次号重复提交可被上游识别为幂等重试。
    """
    return compute_fingerprint(
        {
            "batch_no": input_data.batch_no,
            "variant_id": input_data.variant_id,
            "location_id": input_data.location_id,
        }
    )


class CreateInventoryBatchConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_inventory_batch"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"batch_no", "variant_id", "location_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    default_currency: str = "CNY"


class CreateInventoryBatchInput(BaseModel):
    variant_id: str
    location_id: str
    batch_no: str
    video_url: str
    cost_price_cents: int
    retail_price_cents: int
    stock_qty: int
    currency: str = ""
    media_assets: list[str] = []
    inspected_by: str = ""


async def create_inventory_batch(
    config: CreateInventoryBatchConfig,
    input_data: CreateInventoryBatchInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """质检通过后落库一条批次记录：挂载溯源视频、独立成本/零售价、初始库存。

    质检本身（合格判定）不在本函数职责内 —— 本函数只负责把“质检已完成”这一
    事件持久化为一条 inventory_batch 记录。inspected_by 非空即视为质检通过。

    Args:
        config: CreateInventoryBatchConfig。
        input_data: 批次字段（见 CreateInventoryBatchInput）。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool；为 None 时跳过持久化（dry-run，便于单测）。
        on_step: 进度回调，可选。

    Returns:
        标准 omodul 返回 dict，findings 中含 batch_id / batch_no。
    """
    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.video_url:
            raise ValueError("video_url is required — every batch must carry sourcing video")
        if input_data.stock_qty <= 0:
            raise ValueError("stock_qty must be positive")
        if input_data.cost_price_cents < 0 or input_data.retail_price_cents < 0:
            raise ValueError("prices must be non-negative")

        fp = compute_fingerprint_for(config, input_data)
        trail.record(event="validated", batch_no=input_data.batch_no)

        batch_id = uuid7()
        row = {
            "id": batch_id,
            "batch_no": input_data.batch_no,
            "variant_id": input_data.variant_id,
            "location_id": input_data.location_id,
            "video_url": input_data.video_url,
            "media_assets": json.dumps(input_data.media_assets),
            "cost_price_cents": input_data.cost_price_cents,
            "retail_price_cents": input_data.retail_price_cents,
            "currency": input_data.currency or config.default_currency,
            "stock_qty": input_data.stock_qty,
            "reserved_qty": 0,
            "inspection_status": "passed" if input_data.inspected_by else "pending",
            "inspected_by": input_data.inspected_by or None,
        }

        if pool is not None:
            await insert_one(pool, table="inventory_batch", data=row)
            trail.record(event="persisted", batch_id=batch_id)
        else:
            trail.record(event="persisted_skipped_no_pool", batch_id=batch_id)

        if on_step:
            on_step({"stage": "create_inventory_batch", "status": "done", "batch_id": batch_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            trail=trail,
            trail_path=trail_path,
            batch_id=batch_id,
            batch_no=input_data.batch_no,
            inspection_status=row["inspection_status"],
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
