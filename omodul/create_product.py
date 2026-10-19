"""omodul.create_product — 新建商品主表(SPU),同步搜索索引。

搜索同步是尽力而为(best-effort)——provider 未注册或调用失败不会让商品
创建本身失败(Postgres 里的记录才是唯一真相源,索引落后可以靠后台重建
任务补,不该反过来让核心写路径依赖一个外部搜索服务的可用性)。

Composes:
  - obase.persistence.insert_one
  - obase.provider_registry.ProviderRegistry(取 "search" category 下的
    provider 调 upsert_doc,best-effort)

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CreateProductConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_product"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}

    search_provider_name: str = "log"


class CreateProductInput(BaseModel):
    title: str
    slug: str
    description: str = ""
    category_id: str = ""


async def create_product(
    config: CreateProductConfig,
    input_data: CreateProductInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建商品,尽力同步搜索索引。

    Args:
        config: CreateProductConfig。
        input_data: title / slug / description / category_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 product_id / search_indexed。
    """
    from obase.persistence import insert_one
    from obase.provider_registry import ProviderRegistry
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.title:
            raise ValueError("title is required")
        if not input_data.slug:
            raise ValueError("slug is required")

        product_id = uuid7()
        row = {
            "id": product_id,
            "title": input_data.title,
            "slug": input_data.slug,
            "description": input_data.description or None,
            "category_id": input_data.category_id or None,
        }

        search_indexed = False
        if pool is not None:
            await insert_one(pool, table="product", data=row)
            trail.record(event="persisted", product_id=product_id)

            try:
                provider = ProviderRegistry.get().generic("search", config.search_provider_name)
                search_indexed = await provider.upsert_doc(
                    index="product", document={"id": str(product_id), "title": input_data.title}
                )
                trail.record(event="search_indexed", product_id=product_id)
            except Exception as exc:  # noqa: BLE001 — best-effort, never fails the create
                trail.record(event="search_index_failed", detail=str(exc))
        else:
            trail.record(event="persisted_skipped_no_pool", product_id=product_id)

        if on_step:
            on_step({"stage": "create_product", "status": "done", "product_id": product_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            product_id=product_id,
            search_indexed=search_indexed,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
