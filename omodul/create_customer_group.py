"""omodul.create_customer_group — 新建买家分组。

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: CreateCustomerGroupConfig, input_data: CreateCustomerGroupInput
) -> str:
    """Fingerprint over name。"""
    return compute_fingerprint({"name": input_data.name})


class CreateCustomerGroupConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_customer_group"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"name"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class CreateCustomerGroupInput(BaseModel):
    name: str


async def create_customer_group(
    config: CreateCustomerGroupConfig,
    input_data: CreateCustomerGroupInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建买家分组。

    Args:
        config: CreateCustomerGroupConfig。
        input_data: name。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 group_id。
    """
    from obase.persistence import insert_one
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if not input_data.name:
            raise ValueError("name is required")

        fp = compute_fingerprint_for(config, input_data)

        group_id = uuid7()
        row = {"id": group_id, "name": input_data.name}

        if pool is not None:
            await insert_one(pool, table="customer_group", data=row)
            trail.record(event="persisted", group_id=group_id)
        else:
            trail.record(event="persisted_skipped_no_pool", group_id=group_id)

        if on_step:
            on_step({"stage": "create_customer_group", "status": "done", "group_id": group_id})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            group_id=group_id,
            name=input_data.name,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
