"""omodul.update_product — 局部更新商品,尽力同步搜索索引。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_UPDATABLE_FIELDS = ("title", "slug", "description", "category_id", "status")


class UpdateProductConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_product"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}

    search_provider_name: str = "log"


class UpdateProductInput(BaseModel):
    product_id: str
    title: str | None = None
    slug: str | None = None
    description: str | None = None
    category_id: str | None = None
    status: str | None = None


async def update_product(
    config: UpdateProductConfig,
    input_data: UpdateProductInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新商品,尽力重新同步搜索索引。

    Args:
        config: UpdateProductConfig。
        input_data: product_id + 任意子集的可更新字段。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, update_one
    from obase.provider_registry import ProviderRegistry

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — update_product always touches persisted product")

        updates = {
            f: getattr(input_data, f)
            for f in _UPDATABLE_FIELDS
            if getattr(input_data, f) is not None
        }
        if not updates:
            raise ValueError("at least one field must be provided to update")

        product = await read_one(pool, table="product", id=input_data.product_id)
        if product is None:
            raise ValueError(f"product {input_data.product_id} not found")

        await update_one(pool, table="product", id=input_data.product_id, data=updates)
        trail.record(
            event="product_updated",
            product_id=input_data.product_id,
            fields=list(updates.keys()),
        )

        search_indexed = False
        try:
            provider = ProviderRegistry.get().generic("search", config.search_provider_name)
            search_indexed = await provider.upsert_doc(
                index="product",
                document={
                    "id": input_data.product_id,
                    "title": updates.get("title", product["title"]),
                },
            )
            trail.record(event="search_reindexed", product_id=input_data.product_id)
        except Exception as exc:  # noqa: BLE001 — best-effort, never fails the update
            trail.record(event="search_index_failed", detail=str(exc))

        if on_step:
            on_step(
                {"stage": "update_product", "status": "done", "product_id": input_data.product_id}
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            product_id=input_data.product_id,
            updated_fields=list(updates.keys()),
            search_indexed=search_indexed,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
