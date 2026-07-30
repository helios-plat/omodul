"""omodul.create_product_collection — 新建手工精选集合,可选带初始商品列表。

跟 product_category 的树状分类是两回事——纯人工挑选,一个商品可以同时在
多个集合里,没有层级关系。

Composes:
  - obase.persistence.transaction(建集合 + 挂初始商品同一事务)

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CreateProductCollectionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_product_collection"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateProductCollectionInput(BaseModel):
    name: str
    slug: str
    product_ids: list[str] = []


async def create_product_collection(
    config: CreateProductCollectionConfig,
    input_data: CreateProductCollectionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建精选集合,可选挂一批初始商品。

    Args:
        config: CreateProductCollectionConfig。
        input_data: name / slug / product_ids(可选)。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 collection_id。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.name:
            raise ValueError("name is required")
        if not input_data.slug:
            raise ValueError("slug is required")

        collection_id = uuid7()

        if pool is not None:
            async with transaction(pool) as tx:
                await tx.execute(
                    'INSERT INTO "product_collection" (id, name, slug) VALUES ($1, $2, $3)',
                    collection_id,
                    input_data.name,
                    input_data.slug,
                )
                for product_id in input_data.product_ids:
                    product = await tx.fetchrow(
                        'SELECT id FROM "product" WHERE id = $1 AND deleted_at IS NULL',
                        product_id,
                    )
                    if product is None:
                        raise ValueError(f"product {product_id} not found")
                    await tx.execute(
                        'INSERT INTO "product_collection_item" '
                        "(id, collection_id, product_id) VALUES ($1, $2, $3)",
                        uuid7(),
                        collection_id,
                        product_id,
                    )
            trail.record(
                event="persisted", collection_id=collection_id, items=len(input_data.product_ids)
            )
        else:
            trail.record(event="persisted_skipped_no_pool", collection_id=collection_id)

        if on_step:
            on_step(
                {
                    "stage": "create_product_collection",
                    "status": "done",
                    "collection_id": collection_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            collection_id=collection_id,
            name=input_data.name,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
