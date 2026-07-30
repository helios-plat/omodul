"""omodul.update_sales_channel — 局部更新销售渠道(name/status)。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_UPDATABLE_FIELDS = ("name", "status")


class UpdateSalesChannelConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_sales_channel"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UpdateSalesChannelInput(BaseModel):
    channel_id: str
    name: str | None = None
    status: str | None = None


async def update_sales_channel(
    config: UpdateSalesChannelConfig,
    input_data: UpdateSalesChannelInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新销售渠道。

    Args:
        config: UpdateSalesChannelConfig。
        input_data: channel_id + 任意子集的可更新字段。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — update_sales_channel always touches persisted channel"
            )

        updates = {
            f: getattr(input_data, f)
            for f in _UPDATABLE_FIELDS
            if getattr(input_data, f) is not None
        }
        if not updates:
            raise ValueError("at least one field must be provided to update")

        channel = await read_one(pool, table="sales_channel", id=input_data.channel_id)
        if channel is None:
            raise ValueError(f"sales_channel {input_data.channel_id} not found")

        await update_one(pool, table="sales_channel", id=input_data.channel_id, data=updates)
        trail.record(
            event="channel_updated",
            channel_id=input_data.channel_id,
            fields=list(updates.keys()),
        )

        if on_step:
            on_step(
                {
                    "stage": "update_sales_channel",
                    "status": "done",
                    "channel_id": input_data.channel_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            channel_id=input_data.channel_id,
            updated_fields=list(updates.keys()),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
