"""omodul.create_product_option — 给商品加一个属性键(如"尺码"/"颜色")。

只维护属性键本身,不含具体取值——取值挂在 product_variant.option_values。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CreateProductOptionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_product_option"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateProductOptionInput(BaseModel):
    product_id: str
    name: str


async def create_product_option(
    config: CreateProductOptionConfig,
    input_data: CreateProductOptionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """给商品加一个属性键。

    Args:
        config: CreateProductOptionConfig。
        input_data: product_id / name。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 option_id。
    """
    from obase.persistence import insert_one, read_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.name:
            raise ValueError("name is required")

        if pool is not None:
            product = await read_one(pool, table="product", id=input_data.product_id)
            if product is None:
                raise ValueError(f"product {input_data.product_id} not found")

        option_id = uuid7()
        row = {"id": option_id, "product_id": input_data.product_id, "name": input_data.name}

        if pool is not None:
            await insert_one(pool, table="product_option", data=row)
            trail.record(event="persisted", option_id=option_id)
        else:
            trail.record(event="persisted_skipped_no_pool", option_id=option_id)

        if on_step:
            on_step({"stage": "create_product_option", "status": "done", "option_id": option_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            option_id=option_id,
            name=input_data.name,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
