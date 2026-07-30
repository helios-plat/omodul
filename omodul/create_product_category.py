"""omodul.create_product_category — 新建分类,插入后全树重算 lft/rgt。

嵌套集(nested set)重建法:每次增删改后,把整棵未删除的分类树重新做一遍
DFS 编号(按 name 排序保证结果确定性),而不是增量调整受影响子树的 lft/rgt
——增量算法(插入/移动节点只调整必要范围)复杂度不成比例,树规模预期不大,
全量重建足够快且不容易出 bug。

Composes:
  - obase.persistence.transaction(插入 + 全树重算同一事务)

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CreateProductCategoryConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_product_category"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CreateProductCategoryInput(BaseModel):
    name: str
    slug: str
    parent_id: str | None = None


async def _rebuild_category_tree(tx) -> None:
    """对整棵未删除分类树重新计算 lft/rgt,DFS 编号,同级按 name 排序。"""
    rows = await tx.fetch(
        'SELECT id, parent_id, name FROM "product_category" WHERE deleted_at IS NULL'
    )
    children: dict[Any, list[dict]] = {}
    for row in rows:
        children.setdefault(row["parent_id"], []).append(row)
    for key in children:
        children[key].sort(key=lambda r: r["name"])

    counter = 1
    updates: list[tuple[Any, int, int]] = []

    def visit(node_id: Any) -> None:
        nonlocal counter
        lft = counter
        counter += 1
        for child in children.get(node_id, []):
            visit(child["id"])
        rgt = counter
        counter += 1
        updates.append((node_id, lft, rgt))

    for root in children.get(None, []):
        visit(root["id"])

    for cat_id, lft, rgt in updates:
        await tx.execute(
            'UPDATE "product_category" SET lft = $1, rgt = $2 WHERE id = $3', lft, rgt, cat_id
        )


async def create_product_category(
    config: CreateProductCategoryConfig,
    input_data: CreateProductCategoryInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建分类,插入后重算整棵树的 lft/rgt。

    Args:
        config: CreateProductCategoryConfig。
        input_data: name / slug / parent_id(可选,None 为根节点)。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 category_id。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.name:
            raise ValueError("name is required")
        if not input_data.slug:
            raise ValueError("slug is required")

        category_id = uuid7()

        if pool is not None:
            async with transaction(pool) as tx:
                if input_data.parent_id:
                    parent = await tx.fetchrow(
                        'SELECT id FROM "product_category" WHERE id = $1 AND deleted_at IS NULL',
                        input_data.parent_id,
                    )
                    if parent is None:
                        raise ValueError(f"parent category {input_data.parent_id} not found")

                await tx.execute(
                    'INSERT INTO "product_category" (id, name, slug, parent_id) '
                    "VALUES ($1, $2, $3, $4)",
                    category_id,
                    input_data.name,
                    input_data.slug,
                    input_data.parent_id,
                )
                await _rebuild_category_tree(tx)
            trail.record(event="persisted", category_id=category_id)
        else:
            trail.record(event="persisted_skipped_no_pool", category_id=category_id)

        if on_step:
            on_step(
                {
                    "stage": "create_product_category",
                    "status": "done",
                    "category_id": category_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            category_id=category_id,
            name=input_data.name,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
