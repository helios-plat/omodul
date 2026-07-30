"""omodul.create_discount_rule — 给折扣挂数值规则(1:1,DB UNIQUE(discount_id) 兜底)。

Composes:
  - obase.persistence.insert_one (写入 discount_rule 表)

Pillars: fingerprint
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

_VALID_RULE_TYPES = {"fixed", "percentage", "free_shipping"}


def compute_fingerprint_for(
    config: CreateDiscountRuleConfig, input_data: CreateDiscountRuleInput
) -> str:
    """Fingerprint over discount_id + rule_type。"""
    return compute_fingerprint(
        {"discount_id": input_data.discount_id, "rule_type": input_data.rule_type}
    )


class CreateDiscountRuleConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_discount_rule"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"discount_id", "rule_type"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class CreateDiscountRuleInput(BaseModel):
    discount_id: str
    rule_type: str  # 'fixed' | 'percentage' | 'free_shipping'
    amount_cents: int | None = None
    percent: float | None = None
    min_subtotal_cents: int | None = None
    region_codes: list[str] | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    max_uses: int | None = None


async def create_discount_rule(
    config: CreateDiscountRuleConfig,
    input_data: CreateDiscountRuleInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """给折扣挂上具体打法。amount_cents 仅 fixed 必填,percent 仅 percentage 必填,
    free_shipping 两者都不需要。

    Args:
        config: CreateDiscountRuleConfig。
        input_data: 见 CreateDiscountRuleInput。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 rule_id。
    """
    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if input_data.rule_type not in _VALID_RULE_TYPES:
            raise ValueError(f"invalid rule_type: {input_data.rule_type!r}")
        if input_data.rule_type == "fixed" and not input_data.amount_cents:
            raise ValueError("amount_cents is required for rule_type='fixed'")
        if input_data.rule_type == "percentage" and input_data.percent is None:
            raise ValueError("percent is required for rule_type='percentage'")

        fp = compute_fingerprint_for(config, input_data)

        rule_id = uuid7()
        row = {
            "id": rule_id,
            "discount_id": input_data.discount_id,
            "rule_type": input_data.rule_type,
            "amount_cents": input_data.amount_cents,
            "percent": input_data.percent,
            "min_subtotal_cents": input_data.min_subtotal_cents,
            "region_codes": input_data.region_codes,
            "valid_from": datetime.fromisoformat(input_data.valid_from)
            if input_data.valid_from
            else None,
            "valid_until": datetime.fromisoformat(input_data.valid_until)
            if input_data.valid_until
            else None,
            "max_uses": input_data.max_uses,
            "uses_count": 0,
        }

        if pool is not None:
            await insert_one(pool, table="discount_rule", data=row)
            trail.record(event="persisted", rule_id=rule_id)
        else:
            trail.record(event="persisted_skipped_no_pool", rule_id=rule_id)

        if on_step:
            on_step({"stage": "create_discount_rule", "status": "done", "rule_id": rule_id})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            rule_id=rule_id,
            discount_id=input_data.discount_id,
            rule_type=input_data.rule_type,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
