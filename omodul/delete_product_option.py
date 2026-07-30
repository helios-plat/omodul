"""omodul.delete_product_option — 软删属性键。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class DeleteProductOptionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_product_option"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class DeleteProductOptionInput(BaseModel):
    option_id: str


async def delete_product_option(
    config: DeleteProductOptionConfig,
    input_data: DeleteProductOptionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删属性键。

    Args:
        config: DeleteProductOptionConfig。
        input_data: option_id。
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
                "pool is required — delete_product_option always touches persisted option"
            )

        deleted = await soft_delete_one(pool, table="product_option", id=input_data.option_id)
        if not deleted:
            raise ValueError(f"product_option {input_data.option_id} not found or already deleted")
        trail.record(event="option_deleted", option_id=input_data.option_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_product_option",
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
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
