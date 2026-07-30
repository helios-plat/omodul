"""omodul.delete_tax_rate — 软删税率。

不像 delete_region,没有"前置无订单校验"——历史订单不引用具体 tax_rate_id
(customer_order.tax_cents 是金额快照,不是 FK),删税率不影响历史记录。

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(config: DeleteTaxRateConfig, input_data: DeleteTaxRateInput) -> str:
    """Fingerprint over tax_rate_id。"""
    return compute_fingerprint({"tax_rate_id": input_data.tax_rate_id})


class DeleteTaxRateConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_tax_rate"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"tax_rate_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class DeleteTaxRateInput(BaseModel):
    tax_rate_id: str


async def delete_tax_rate(
    config: DeleteTaxRateConfig,
    input_data: DeleteTaxRateInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删税率。

    Args:
        config: DeleteTaxRateConfig。
        input_data: tax_rate_id。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import soft_delete_one

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — delete_tax_rate always touches persisted tax_rate")

        fp = compute_fingerprint_for(config, input_data)

        deleted = await soft_delete_one(pool, table="tax_rate", id=input_data.tax_rate_id)
        if not deleted:
            raise ValueError(f"tax_rate {input_data.tax_rate_id} not found or already deleted")
        trail.record(event="tax_rate_deleted", tax_rate_id=input_data.tax_rate_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_tax_rate",
                    "status": "done",
                    "tax_rate_id": input_data.tax_rate_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            tax_rate_id=input_data.tax_rate_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
