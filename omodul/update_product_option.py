"""omodul.update_product_option — 改属性键名字。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class UpdateProductOptionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_product_option"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UpdateProductOptionInput(BaseModel):
    option_id: str
    name: str


async def update_product_option(
    config: UpdateProductOptionConfig,
    input_data: UpdateProductOptionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """改属性键名字。

    Args:
        config: UpdateProductOptionConfig。
        input_data: option_id / name。
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
                "pool is required — update_product_option always touches persisted option"
            )
        if not input_data.name:
            raise ValueError("name is required")

        option = await read_one(pool, table="product_option", id=input_data.option_id)
        if option is None:
            raise ValueError(f"product_option {input_data.option_id} not found")

        await update_one(
            pool, table="product_option", id=input_data.option_id, data={"name": input_data.name}
        )
        trail.record(event="option_updated", option_id=input_data.option_id)

        if on_step:
            on_step(
                {
                    "stage": "update_product_option",
                    "status": "done",
                    "option_id": input_data.option_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            option_id=input_data.option_id,
            name=input_data.name,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
