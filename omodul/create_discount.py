"""omodul.create_discount — 折扣壳(code + 状态),数值规则由 create_discount_rule 挂载。

Composes:
  - obase.uuid7.uuid7 (discount id)
  - obase.persistence.insert_one (写入 discount 表)

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(config: CreateDiscountConfig, input_data: CreateDiscountInput) -> str:
    """Fingerprint over code —— 同一 code 重复提交可被上游识别为幂等重试。"""
    return compute_fingerprint({"code": input_data.code})


class CreateDiscountConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_discount"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"code"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class CreateDiscountInput(BaseModel):
    code: str


async def create_discount(
    config: CreateDiscountConfig,
    input_data: CreateDiscountInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建一张折扣壳(仅 code + status='active'),数值规则需另调 create_discount_rule。

    Args:
        config: CreateDiscountConfig。
        input_data: code。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 discount_id。
    """
    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.code:
            raise ValueError("code is required")

        fp = compute_fingerprint_for(config, input_data)

        discount_id = uuid7()
        row = {"id": discount_id, "code": input_data.code, "status": "active"}

        if pool is not None:
            await insert_one(pool, table="discount", data=row)
            trail.record(event="persisted", discount_id=discount_id)
        else:
            trail.record(event="persisted_skipped_no_pool", discount_id=discount_id)

        if on_step:
            on_step({"stage": "create_discount", "status": "done", "discount_id": discount_id})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            discount_id=discount_id,
            code=input_data.code,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
