"""omodul.delete_product — 软删商品,尽力从搜索索引移除。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class DeleteProductConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_product"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}

    search_provider_name: str = "log"


class DeleteProductInput(BaseModel):
    product_id: str


async def delete_product(
    config: DeleteProductConfig,
    input_data: DeleteProductInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删商品,尽力从搜索索引移除。

    Args:
        config: DeleteProductConfig。
        input_data: product_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import soft_delete_one
    from obase.provider_registry import ProviderRegistry

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — delete_product always touches persisted product")

        deleted = await soft_delete_one(pool, table="product", id=input_data.product_id)
        if not deleted:
            raise ValueError(f"product {input_data.product_id} not found or already deleted")
        trail.record(event="product_deleted", product_id=input_data.product_id)

        search_deindexed = False
        try:
            provider = ProviderRegistry.get().generic("search", config.search_provider_name)
            search_deindexed = await provider.delete_doc(
                index="product", doc_id=input_data.product_id
            )
            trail.record(event="search_deindexed", product_id=input_data.product_id)
        except Exception as exc:  # noqa: BLE001 — best-effort, never fails the delete
            trail.record(event="search_deindex_failed", detail=str(exc))

        if on_step:
            on_step(
                {"stage": "delete_product", "status": "done", "product_id": input_data.product_id}
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            product_id=input_data.product_id,
            search_deindexed=search_deindexed,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
