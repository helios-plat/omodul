"""omodul.update_gift_card — 更新礼品卡元数据(status/expires_at)。

不接受 balance_cents 修改——余额只能通过 apply_gift_card_to_cart /
remove_gift_card_from_cart 在事务里增减,不给通用 update 开后门,避免绕过
"扣减必须对应一次真实核销"的审计约束。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result

_UPDATABLE_FIELDS = ("status", "expires_at")


class UpdateGiftCardConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_gift_card"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class UpdateGiftCardInput(BaseModel):
    gift_card_id: str
    status: str | None = None  # 'active' | 'inactive'
    expires_at: str | None = None


async def update_gift_card(
    config: UpdateGiftCardConfig,
    input_data: UpdateGiftCardInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新礼品卡元数据(仅 status/expires_at)。

    Args:
        config: UpdateGiftCardConfig。
        input_data: gift_card_id + 任意子集的可更新字段。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if input_data.status is not None and input_data.status not in ("active", "inactive"):
            raise ValueError(f"invalid status: {input_data.status!r}")
        if pool is None:
            raise ValueError(
                "pool is required — update_gift_card always touches persisted gift_card"
            )

        updates = {
            f: getattr(input_data, f)
            for f in _UPDATABLE_FIELDS
            if getattr(input_data, f) is not None
        }
        if not updates:
            raise ValueError("at least one field must be provided to update")

        gift_card = await read_one(pool, table="gift_card", id=input_data.gift_card_id)
        if gift_card is None:
            raise ValueError(f"gift_card {input_data.gift_card_id} not found")

        await update_one(pool, table="gift_card", id=input_data.gift_card_id, data=updates)
        trail.record(
            event="gift_card_updated", gift_card_id=input_data.gift_card_id, fields=list(updates)
        )

        if on_step:
            on_step(
                {
                    "stage": "update_gift_card",
                    "status": "done",
                    "gift_card_id": input_data.gift_card_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            gift_card_id=input_data.gift_card_id,
            updated_fields=list(updates),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
