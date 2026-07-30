"""omodul.create_customer — 新建买家主账号。

Composes:
  - obase.uuid7.uuid7 + obase.persistence.insert_one

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(config: CreateCustomerConfig, input_data: CreateCustomerInput) -> str:
    """Fingerprint over email。"""
    return compute_fingerprint({"email": input_data.email})


class CreateCustomerConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_customer"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"email"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class CreateCustomerInput(BaseModel):
    email: str
    phone: str = ""
    name: str = ""


async def create_customer(
    config: CreateCustomerConfig,
    input_data: CreateCustomerInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建买家账号。

    Args:
        config: CreateCustomerConfig。
        input_data: email / phone / name。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 customer_id。
    """
    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.email:
            raise ValueError("email is required")

        fp = compute_fingerprint_for(config, input_data)

        customer_id = uuid7()
        row = {
            "id": customer_id,
            "email": input_data.email,
            "phone": input_data.phone or None,
            "name": input_data.name or None,
        }

        if pool is not None:
            await insert_one(pool, table="customer", data=row)
            trail.record(event="persisted", customer_id=customer_id)
        else:
            trail.record(event="persisted_skipped_no_pool", customer_id=customer_id)

        if on_step:
            on_step({"stage": "create_customer", "status": "done", "customer_id": customer_id})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            customer_id=customer_id,
            email=input_data.email,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
