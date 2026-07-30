"""omodul.delete_gift_card — 软删礼品卡(冻结),高审计。

不允许删除仍有余额的礼品卡——防止误删导致持卡人余额凭空消失且无退款
流程兜底;要作废剩余余额需先走业务上的退款/核销,而不是直接删卡。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class DeleteGiftCardConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_gift_card"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class DeleteGiftCardInput(BaseModel):
    gift_card_id: str


async def delete_gift_card(
    config: DeleteGiftCardConfig,
    input_data: DeleteGiftCardInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删一张礼品卡。余额非零时拒绝(见模块 docstring)。

    Args:
        config: DeleteGiftCardConfig。
        input_data: gift_card_id。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, soft_delete_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — delete_gift_card always touches persisted gift_card"
            )

        gift_card = await read_one(pool, table="gift_card", id=input_data.gift_card_id)
        if gift_card is None:
            raise ValueError(f"gift_card {input_data.gift_card_id} not found")
        if gift_card["balance_cents"] > 0:
            raise ValueError(
                f"gift_card {input_data.gift_card_id} still has a nonzero balance "
                f"({gift_card['balance_cents']} cents) — cannot delete"
            )

        deleted = await soft_delete_one(pool, table="gift_card", id=input_data.gift_card_id)
        if not deleted:
            raise ValueError(f"gift_card {input_data.gift_card_id} not found or already deleted")
        trail.record(event="soft_deleted", gift_card_id=input_data.gift_card_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_gift_card",
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
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
