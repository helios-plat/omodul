"""omodul.create_tax_rate — 给区域挂一条税率。

Composes:
  - obase.persistence.read_one(校验 region_code 存在)+ insert_one

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(config: CreateTaxRateConfig, input_data: CreateTaxRateInput) -> str:
    """Fingerprint over region_code + name。"""
    return compute_fingerprint({"region_code": input_data.region_code, "name": input_data.name})


class CreateTaxRateConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_tax_rate"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"region_code", "name"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class CreateTaxRateInput(BaseModel):
    region_code: str
    name: str
    rate_percent: float


async def create_tax_rate(
    config: CreateTaxRateConfig,
    input_data: CreateTaxRateInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """给区域新建一条税率。

    Args:
        config: CreateTaxRateConfig。
        input_data: region_code / name / rate_percent。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 tax_rate_id。
    """
    from obase.persistence import insert_one, read_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if input_data.rate_percent < 0 or input_data.rate_percent > 100:
            raise ValueError("rate_percent must be within [0, 100]")

        fp = compute_fingerprint_for(config, input_data)

        if pool is not None:
            region = await read_one(
                pool, table="region", id=input_data.region_code, id_column="code"
            )
            if region is None:
                raise ValueError(f"region {input_data.region_code!r} not found")

        tax_rate_id = uuid7()
        row = {
            "id": tax_rate_id,
            "region_code": input_data.region_code,
            "name": input_data.name,
            "rate_percent": input_data.rate_percent,
        }

        if pool is not None:
            await insert_one(pool, table="tax_rate", data=row)
            trail.record(event="persisted", tax_rate_id=tax_rate_id)
        else:
            trail.record(event="persisted_skipped_no_pool", tax_rate_id=tax_rate_id)

        if on_step:
            on_step({"stage": "create_tax_rate", "status": "done", "tax_rate_id": tax_rate_id})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            tax_rate_id=tax_rate_id,
            region_code=input_data.region_code,
            rate_percent=input_data.rate_percent,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
