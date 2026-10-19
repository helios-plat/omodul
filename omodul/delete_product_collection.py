"""omodul.delete_product_collection — 软删集合。

不清理 product_collection_item 关联行——集合本身软删后按 deleted_at IS NULL
过滤查询就查不到了，关联行留着不产生可见的脏数据，也省一次没必要的清理。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class DeleteProductCollectionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_product_collection"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class DeleteProductCollectionInput(BaseModel):
    collection_id: str


async def delete_product_collection(
    config: DeleteProductCollectionConfig,
    input_data: DeleteProductCollectionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删集合。

    Args:
        config: DeleteProductCollectionConfig。
        input_data: collection_id。
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
                "pool is required — delete_product_collection always touches persisted collection"
            )

        deleted = await soft_delete_one(
            pool, table="product_collection", id=input_data.collection_id
        )
        if not deleted:
            raise ValueError(
                f"product_collection {input_data.collection_id} not found or already deleted"
            )
        trail.record(event="collection_deleted", collection_id=input_data.collection_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_product_collection",
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
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
