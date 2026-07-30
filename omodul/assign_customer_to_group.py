"""omodul.assign_customer_to_group — 把买家指派到一个分组(单分组归属)。

不是多对多——customer.customer_group_id 是单一外键,再次调用直接覆盖旧分组
(简化模型,SPEC 只要求"assign",没要求同时属于多个分组)。

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: AssignCustomerToGroupConfig, input_data: AssignCustomerToGroupInput
) -> str:
    """Fingerprint over customer_id + group_id。"""
    return compute_fingerprint(
        {"customer_id": input_data.customer_id, "group_id": input_data.group_id}
    )


class AssignCustomerToGroupConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "assign_customer_to_group"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"customer_id", "group_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class AssignCustomerToGroupInput(BaseModel):
    customer_id: str
    group_id: str


async def assign_customer_to_group(
    config: AssignCustomerToGroupConfig,
    input_data: AssignCustomerToGroupInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """把买家指派到分组(覆盖式,不追加)。

    Args:
        config: AssignCustomerToGroupConfig。
        input_data: customer_id / group_id。
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
                "pool is required — assign_customer_to_group always touches persisted customer"
            )

        fp = compute_fingerprint_for(config, input_data)

        customer = await read_one(pool, table="customer", id=input_data.customer_id)
        if customer is None:
            raise ValueError(f"customer {input_data.customer_id} not found")

        group = await read_one(pool, table="customer_group", id=input_data.group_id)
        if group is None:
            raise ValueError(f"customer_group {input_data.group_id} not found")

        await update_one(
            pool,
            table="customer",
            id=input_data.customer_id,
            data={"customer_group_id": input_data.group_id},
        )
        trail.record(
            event="customer_assigned_to_group",
            customer_id=input_data.customer_id,
            group_id=input_data.group_id,
        )

        if on_step:
            on_step(
                {
                    "stage": "assign_customer_to_group",
                    "status": "done",
                    "customer_id": input_data.customer_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            customer_id=input_data.customer_id,
            group_id=input_data.group_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
