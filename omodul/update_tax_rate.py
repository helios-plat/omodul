"""omodul.update_tax_rate — 局部更新税率(name/rate_percent/status)。

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

_UPDATABLE_FIELDS = ("name", "rate_percent", "status")


def compute_fingerprint_for(config: UpdateTaxRateConfig, input_data: UpdateTaxRateInput) -> str:
    """Fingerprint over tax_rate_id + 待更新字段快照。"""
    return compute_fingerprint(
        {
            "tax_rate_id": input_data.tax_rate_id,
            **{f: getattr(input_data, f) for f in _UPDATABLE_FIELDS},
        }
    )


class UpdateTaxRateConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_tax_rate"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"tax_rate_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class UpdateTaxRateInput(BaseModel):
    tax_rate_id: str
    name: str | None = None
    rate_percent: float | None = None
    status: str | None = None


async def update_tax_rate(
    config: UpdateTaxRateConfig,
    input_data: UpdateTaxRateInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新税率字段。

    Args:
        config: UpdateTaxRateConfig。
        input_data: tax_rate_id + 任意子集的可更新字段。
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
            raise ValueError("pool is required — update_tax_rate always touches persisted tax_rate")

        if input_data.rate_percent is not None and (
            input_data.rate_percent < 0 or input_data.rate_percent > 100
        ):
            raise ValueError("rate_percent must be within [0, 100]")

        updates = {
            f: getattr(input_data, f)
            for f in _UPDATABLE_FIELDS
            if getattr(input_data, f) is not None
        }
        if not updates:
            raise ValueError("at least one field must be provided to update")

        fp = compute_fingerprint_for(config, input_data)

        tax_rate = await read_one(pool, table="tax_rate", id=input_data.tax_rate_id)
        if tax_rate is None:
            raise ValueError(f"tax_rate {input_data.tax_rate_id} not found")

        await update_one(pool, table="tax_rate", id=input_data.tax_rate_id, data=updates)
        trail.record(
            event="tax_rate_updated",
            tax_rate_id=input_data.tax_rate_id,
            fields=list(updates.keys()),
        )

        if on_step:
            on_step(
                {
                    "stage": "update_tax_rate",
                    "status": "done",
                    "tax_rate_id": input_data.tax_rate_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            tax_rate_id=input_data.tax_rate_id,
            updated_fields=list(updates.keys()),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
