"""omodul.cancel_claim — 取消/驳回尚未处理的客诉索赔。

SPEC 只给了 create/cancel/fulfill 三个元素,没有单独的 approve/reject——
本函数同时承载"客户撤回"和"商家驳回"两种业务含义,都落 canceled(自由
裁量设计)。claim_type='replace' 时释放 new_items 的预留库存。

Pillars: decision_trail
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CancelClaimConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "cancel_claim"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CancelClaimInput(BaseModel):
    claim_id: str


async def cancel_claim(
    config: CancelClaimConfig,
    input_data: CancelClaimInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """取消/驳回一笔尚未处理的客诉索赔。

    Args:
        config: CancelClaimConfig。
        input_data: claim_id。
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
            raise ValueError("pool is required — cancel_claim always touches persisted claim")

        async with transaction(pool) as tx:
            claim = await tx.fetchrow(
                'SELECT * FROM "claim" WHERE id = $1 FOR UPDATE', input_data.claim_id
            )
            if claim is None:
                raise ValueError(f"claim {input_data.claim_id} not found")
            if claim["status"] != "pending":
                raise ValueError(
                    f"claim {input_data.claim_id} cannot be canceled from status "
                    f"{claim['status']!r}"
                )

            if claim["claim_type"] == "replace" and claim["new_items"]:
                for item in json.loads(claim["new_items"]):
                    await tx.execute(
                        'UPDATE "inventory_batch" SET reserved_qty = reserved_qty - $1, '
                        "updated_at = NOW() WHERE id = $2",
                        item["quantity"],
                        item["batch_id"],
                    )
                trail.record(event="new_items_released")

            await tx.execute(
                "UPDATE \"claim\" SET status = 'canceled', updated_at = NOW() WHERE id = $1",
                input_data.claim_id,
            )
            trail.record(event="claim_canceled", claim_id=input_data.claim_id)

        if on_step:
            on_step({"stage": "cancel_claim", "status": "done", "claim_id": input_data.claim_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            claim_id=input_data.claim_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
