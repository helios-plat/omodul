"""omodul.update_discount_rule — 更新折扣规则数值(不改 rule_type/discount_id/uses_count)。

uses_count 只由 apply_discount_to_cart/remove_discount_from_cart 内部维护,
不对外暴露修改入口,避免调用方绕过用量上限。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

_DATETIME_FIELDS = {"valid_from", "valid_until"}
_UPDATABLE_FIELDS = (
    "amount_cents",
    "percent",
    "min_subtotal_cents",
    "region_codes",
    "valid_from",
    "valid_until",
    "max_uses",
)


def compute_fingerprint_for(
    config: UpdateDiscountRuleConfig, input_data: UpdateDiscountRuleInput
) -> str:
    """Fingerprint over rule_id + 待更新字段快照。"""
    return compute_fingerprint(
        {
            "rule_id": input_data.rule_id,
            **{f: getattr(input_data, f) for f in _UPDATABLE_FIELDS},
        }
    )


class UpdateDiscountRuleConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_discount_rule"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"rule_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class UpdateDiscountRuleInput(BaseModel):
    rule_id: str
    amount_cents: int | None = None
    percent: float | None = None
    min_subtotal_cents: int | None = None
    region_codes: list[str] | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    max_uses: int | None = None


async def update_discount_rule(
    config: UpdateDiscountRuleConfig,
    input_data: UpdateDiscountRuleInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新折扣规则数值——只有显式传入(非 None)的字段才会被写入。

    Args:
        config: UpdateDiscountRuleConfig。
        input_data: rule_id + 任意子集的可更新字段。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import read_one, update_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — update_discount_rule always touches persisted rule"
            )

        updates = {}
        for f in _UPDATABLE_FIELDS:
            value = getattr(input_data, f)
            if value is None:
                continue
            updates[f] = datetime.fromisoformat(value) if f in _DATETIME_FIELDS else value
        if not updates:
            raise ValueError("at least one field must be provided to update")

        fp = compute_fingerprint_for(config, input_data)

        rule = await read_one(pool, table="discount_rule", id=input_data.rule_id)
        if rule is None:
            raise ValueError(f"discount_rule {input_data.rule_id} not found")

        await update_one(pool, table="discount_rule", id=input_data.rule_id, data=updates)
        trail.record(event="rule_updated", rule_id=input_data.rule_id, fields=list(updates))

        if on_step:
            on_step(
                {
                    "stage": "update_discount_rule",
                    "status": "done",
                    "rule_id": input_data.rule_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            rule_id=input_data.rule_id,
            updated_fields=list(updates),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
