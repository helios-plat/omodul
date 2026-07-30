"""omodul.update_product_category — 局部更新分类,parent_id 变了就重算全树。

改 parent_id 时会做环检测(新父节点不能是自己或自己的后代),否则嵌套集
树会出现循环引用,DFS 重建会死循环。

Pillars: report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_UPDATABLE_FIELDS = ("name", "slug", "parent_id", "status")


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


class UpdateProductCategoryConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_product_category"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UpdateProductCategoryInput(BaseModel):
    category_id: str
    name: str | None = None
    slug: str | None = None
    parent_id: str | None = None
    status: str | None = None


async def update_product_category(
    config: UpdateProductCategoryConfig,
    input_data: UpdateProductCategoryInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新分类;parent_id 若变化,校验无环后重算整棵树。

    Args:
        config: UpdateProductCategoryConfig。
        input_data: category_id + 任意子集的可更新字段。
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
                "pool is required — update_product_category always touches persisted category"
            )

        updates = {
            f: getattr(input_data, f)
            for f in _UPDATABLE_FIELDS
            if getattr(input_data, f) is not None
        }
        parent_changed = "parent_id" in updates
        if not updates:
            raise ValueError("at least one field must be provided to update")

        async with transaction(pool) as tx:
            category = await tx.fetchrow(
                'SELECT * FROM "product_category" WHERE id = $1 AND deleted_at IS NULL',
                input_data.category_id,
            )
            if category is None:
                raise ValueError(f"product_category {input_data.category_id} not found")

            if parent_changed and input_data.parent_id:
                if input_data.parent_id == input_data.category_id:
                    raise ValueError("a category cannot be its own parent")
                descendants = await tx.fetch(
                    'SELECT id FROM "product_category" WHERE lft > $1 AND rgt < $2 '
                    "AND deleted_at IS NULL",
                    category["lft"],
                    category["rgt"],
                )
                if any(str(d["id"]) == input_data.parent_id for d in descendants):
                    raise ValueError("cannot move a category under its own descendant")

            set_clause = ", ".join(f'"{k}" = ${i + 2}' for i, k in enumerate(updates))
            await tx.execute(
                f'UPDATE "product_category" SET {set_clause}, updated_at = NOW() WHERE id = $1',
                input_data.category_id,
                *updates.values(),
            )

            if parent_changed:
                await _rebuild_category_tree(tx)

            trail.record(
                event="category_updated",
                category_id=input_data.category_id,
                fields=list(updates.keys()),
            )

        if on_step:
            on_step(
                {
                    "stage": "update_product_category",
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
            updated_fields=list(updates.keys()),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
