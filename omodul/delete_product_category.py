"""omodul.delete_product_category — 软删分类,重算全树,拒绝仍有子分类的节点。

有非删除状态子分类的节点不允许删除(会留下指向"消失节点"的孤儿子树)——
要删必须先删光子分类,或者先把子分类挪到别的父节点下(update_product_category
改 parent_id)。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class DeleteProductCategoryConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_product_category"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class DeleteProductCategoryInput(BaseModel):
    category_id: str


async def _rebuild_category_tree(tx: Any) -> None:
    """对整棵未删除分类树重新计算 lft/rgt,DFS 编号,同级按 name 排序。

    跟 create_product_category._rebuild_category_tree 是同一份逻辑——
    omodul 元素之间禁止裸调(Layer-3 discipline),所以这里重复一份而不是
    跨文件 import,不是疏忽。
    """
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


async def delete_product_category(
    config: DeleteProductCategoryConfig,
    input_data: DeleteProductCategoryInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删分类(拒绝仍有子分类的节点),重算整棵树。

    Args:
        config: DeleteProductCategoryConfig。
        input_data: category_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import transaction

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — delete_product_category always touches persisted category"
            )

        async with transaction(pool) as tx:
            category = await tx.fetchrow(
                'SELECT * FROM "product_category" WHERE id = $1 AND deleted_at IS NULL',
                input_data.category_id,
            )
            if category is None:
                raise ValueError(f"product_category {input_data.category_id} not found")

            child_count = await tx.fetchval(
                'SELECT COUNT(*) FROM "product_category" WHERE parent_id = $1 '
                "AND deleted_at IS NULL",
                input_data.category_id,
            )
            if child_count > 0:
                raise ValueError(
                    f"product_category {input_data.category_id} has {child_count} "
                    "non-deleted child categories — delete or move them first"
                )

            await tx.execute(
                'UPDATE "product_category" SET deleted_at = NOW(), updated_at = NOW() '
                "WHERE id = $1",
                input_data.category_id,
            )
            await _rebuild_category_tree(tx)
            trail.record(event="category_deleted", category_id=input_data.category_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_product_category",
                    "status": "done",
                    "category_id": input_data.category_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            category_id=input_data.category_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
