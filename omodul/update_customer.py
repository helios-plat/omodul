"""omodul.update_customer — 局部更新买家账号(email/phone/name/status)。

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

_UPDATABLE_FIELDS = ("email", "phone", "name", "status")


def compute_fingerprint_for(config: UpdateCustomerConfig, input_data: UpdateCustomerInput) -> str:
    """Fingerprint over customer_id + 待更新字段快照。"""
    return compute_fingerprint(
        {
            "customer_id": input_data.customer_id,
            **{f: getattr(input_data, f) for f in _UPDATABLE_FIELDS},
        }
    )


class UpdateCustomerConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_customer"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"customer_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class UpdateCustomerInput(BaseModel):
    customer_id: str
    email: str | None = None
    phone: str | None = None
    name: str | None = None
    status: str | None = None


async def update_customer(
    config: UpdateCustomerConfig,
    input_data: UpdateCustomerInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新买家账号。

    Args:
        config: UpdateCustomerConfig。
        input_data: customer_id + 任意子集的可更新字段。
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
            raise ValueError("pool is required — update_customer always touches persisted customer")

        updates = {
            f: getattr(input_data, f)
            for f in _UPDATABLE_FIELDS
            if getattr(input_data, f) is not None
        }
        if not updates:
            raise ValueError("at least one field must be provided to update")

        fp = compute_fingerprint_for(config, input_data)

        customer = await read_one(pool, table="customer", id=input_data.customer_id)
        if customer is None:
            raise ValueError(f"customer {input_data.customer_id} not found")

        await update_one(pool, table="customer", id=input_data.customer_id, data=updates)
        trail.record(
            event="customer_updated",
            customer_id=input_data.customer_id,
            fields=list(updates.keys()),
        )

        if on_step:
            on_step(
                {
                    "stage": "update_customer",
                    "status": "done",
                    "customer_id": input_data.customer_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            customer_id=input_data.customer_id,
            updated_fields=list(updates.keys()),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
