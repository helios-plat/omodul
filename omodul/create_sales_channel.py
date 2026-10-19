"""omodul.create_sales_channel — 新建销售渠道(如"线下门店 POS"/"线上小程序")。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CreateSalesChannelConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_sales_channel"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateSalesChannelInput(BaseModel):
    name: str


async def create_sales_channel(
    config: CreateSalesChannelConfig,
    input_data: CreateSalesChannelInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建销售渠道。

    Args:
        config: CreateSalesChannelConfig。
        input_data: name。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 channel_id。
    """
    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.name:
            raise ValueError("name is required")

        channel_id = uuid7()
        row = {"id": channel_id, "name": input_data.name}

        if pool is not None:
            await insert_one(pool, table="sales_channel", data=row)
            trail.record(event="persisted", channel_id=channel_id)
        else:
            trail.record(event="persisted_skipped_no_pool", channel_id=channel_id)

        if on_step:
            on_step({"stage": "create_sales_channel", "status": "done", "channel_id": channel_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            channel_id=channel_id,
            name=input_data.name,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
