"""omodul.delete_sales_channel — 软删销售渠道(不清理 sales_channel_product,
同 product_collection 先例)。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class DeleteSalesChannelConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_sales_channel"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class DeleteSalesChannelInput(BaseModel):
    channel_id: str


async def delete_sales_channel(
    config: DeleteSalesChannelConfig,
    input_data: DeleteSalesChannelInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删销售渠道。

    Args:
        config: DeleteSalesChannelConfig。
        input_data: channel_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import soft_delete_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — delete_sales_channel always touches persisted channel"
            )

        deleted = await soft_delete_one(pool, table="sales_channel", id=input_data.channel_id)
        if not deleted:
            raise ValueError(f"sales_channel {input_data.channel_id} not found or already deleted")
        trail.record(event="channel_deleted", channel_id=input_data.channel_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_sales_channel",
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
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
