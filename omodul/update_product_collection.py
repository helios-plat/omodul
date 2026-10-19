"""omodul.update_product_collection — 局部更新集合元数据,可选整体替换商品列表。

product_ids 若提供,是整体替换(先清空该集合全部关联行,再按新列表重建)——
"手工精选"场景里运营通常是"这是现在完整的名单",不是增量加/删,匹配
create_draft_order 那类"整批给全量"的既有约定。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_UPDATABLE_FIELDS = ("name", "slug", "status")


class UpdateProductCollectionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_product_collection"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UpdateProductCollectionInput(BaseModel):
    collection_id: str
    name: str | None = None
    slug: str | None = None
    status: str | None = None
    product_ids: list[str] | None = None


async def update_product_collection(
    config: UpdateProductCollectionConfig,
    input_data: UpdateProductCollectionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新集合,product_ids 若提供则整体替换成员。

    Args:
        config: UpdateProductCollectionConfig。
        input_data: collection_id + 任意子集的可更新字段 + 可选 product_ids。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — update_product_collection always touches persisted collection"
            )

        updates = {
            f: getattr(input_data, f)
            for f in _UPDATABLE_FIELDS
            if getattr(input_data, f) is not None
        }
        if not updates and input_data.product_ids is None:
            raise ValueError("at least one field must be provided to update")

        async with transaction(pool) as tx:
            collection = await tx.fetchrow(
                'SELECT id FROM "product_collection" WHERE id = $1 AND deleted_at IS NULL',
                input_data.collection_id,
            )
            if collection is None:
                raise ValueError(f"product_collection {input_data.collection_id} not found")

            if updates:
                set_clause = ", ".join(f'"{k}" = ${i + 2}' for i, k in enumerate(updates))
                await tx.execute(
                    f'UPDATE "product_collection" SET {set_clause}, updated_at = NOW() '
                    "WHERE id = $1",
                    input_data.collection_id,
                    *updates.values(),
                )

            if input_data.product_ids is not None:
                await tx.execute(
                    'DELETE FROM "product_collection_item" WHERE collection_id = $1',
                    input_data.collection_id,
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
                        input_data.collection_id,
                        product_id,
                    )

            trail.record(
                event="collection_updated",
                collection_id=input_data.collection_id,
                fields=list(updates.keys()),
                items_replaced=input_data.product_ids is not None,
            )

        if on_step:
            on_step(
                {
                    "stage": "update_product_collection",
                    "status": "done",
                    "collection_id": input_data.collection_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            collection_id=input_data.collection_id,
            updated_fields=list(updates.keys()),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
