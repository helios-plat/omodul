"""omodul.create_gift_card — 发行一张礼品卡。高审计:签发即挂 decision_trail。

Composes:
  - obase.uuid7.uuid7 (gift_card id)
  - obase.persistence.insert_one (写入 gift_card 表)

Pillars: decision_trail
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CreateGiftCardConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_gift_card"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}

    default_currency: str = "CNY"


class CreateGiftCardInput(BaseModel):
    code: str
    initial_balance_cents: int
    currency: str = ""
    expires_at: str | None = None


async def create_gift_card(
    config: CreateGiftCardConfig,
    input_data: CreateGiftCardInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """签发一张礼品卡:balance_cents 初始等于 initial_balance_cents。

    Args:
        config: CreateGiftCardConfig。
        input_data: code / initial_balance_cents / currency / expires_at。
        output_dir: decision_trail 落盘目录 —— 数字现金签发是高审计动作,必须留痕。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 gift_card_id。
    """
    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if input_data.initial_balance_cents <= 0:
            raise ValueError("initial_balance_cents must be positive")

        trail.record(event="validated", code=input_data.code)

        gift_card_id = uuid7()
        row = {
            "id": gift_card_id,
            "code": input_data.code,
            "initial_balance_cents": input_data.initial_balance_cents,
            "balance_cents": input_data.initial_balance_cents,
            "currency": input_data.currency or config.default_currency,
            "expires_at": (
                datetime.fromisoformat(input_data.expires_at) if input_data.expires_at else None
            ),
        }

        if pool is not None:
            await insert_one(pool, table="gift_card", data=row)
            trail.record(
                event="issued", gift_card_id=gift_card_id, amount_cents=row["balance_cents"]
            )
        else:
            trail.record(event="issue_skipped_no_pool", gift_card_id=gift_card_id)

        if on_step:
            on_step({"stage": "create_gift_card", "status": "done", "gift_card_id": gift_card_id})

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            gift_card_id=gift_card_id,
            balance_cents=row["balance_cents"],
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
