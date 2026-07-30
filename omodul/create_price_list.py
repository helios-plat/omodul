"""omodul.create_price_list — 新建价格表元数据(具体 SKU 特价见 add_prices_to_list)。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CreatePriceListConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_price_list"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}

    default_currency: str = "CNY"


class CreatePriceListInput(BaseModel):
    name: str
    currency: str = ""
    starts_at: str | None = None
    ends_at: str | None = None


async def create_price_list(
    config: CreatePriceListConfig,
    input_data: CreatePriceListInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建价格表元数据。

    Args:
        config: CreatePriceListConfig。
        input_data: name / currency / starts_at / ends_at(ISO 时间字符串)。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 price_list_id。
    """
    from datetime import datetime

    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.name:
            raise ValueError("name is required")

        price_list_id = uuid7()
        row = {
            "id": price_list_id,
            "name": input_data.name,
            "currency": input_data.currency or config.default_currency,
            "starts_at": datetime.fromisoformat(input_data.starts_at)
            if input_data.starts_at
            else None,
            "ends_at": datetime.fromisoformat(input_data.ends_at) if input_data.ends_at else None,
        }

        if pool is not None:
            await insert_one(pool, table="price_list", data=row)
            trail.record(event="persisted", price_list_id=price_list_id)
        else:
            trail.record(event="persisted_skipped_no_pool", price_list_id=price_list_id)

        if on_step:
            on_step(
                {"stage": "create_price_list", "status": "done", "price_list_id": price_list_id}
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            price_list_id=price_list_id,
            name=input_data.name,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
