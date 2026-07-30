"""omodul.delete_customer_address — 软删一条买家地址。

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: DeleteCustomerAddressConfig, input_data: DeleteCustomerAddressInput
) -> str:
    """Fingerprint over address_id。"""
    return compute_fingerprint({"address_id": input_data.address_id})


class DeleteCustomerAddressConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "delete_customer_address"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"address_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class DeleteCustomerAddressInput(BaseModel):
    address_id: str


async def delete_customer_address(
    config: DeleteCustomerAddressConfig,
    input_data: DeleteCustomerAddressInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """软删一条买家地址。

    Args:
        config: DeleteCustomerAddressConfig。
        input_data: address_id。
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
            raise ValueError(
                "pool is required — delete_customer_address always touches persisted address"
            )

        fp = compute_fingerprint_for(config, input_data)

        deleted = await soft_delete_one(pool, table="customer_address", id=input_data.address_id)
        if not deleted:
            raise ValueError(
                f"customer_address {input_data.address_id} not found or already deleted"
            )
        trail.record(event="address_deleted", address_id=input_data.address_id)

        if on_step:
            on_step(
                {
                    "stage": "delete_customer_address",
                    "status": "done",
                    "address_id": input_data.address_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            address_id=input_data.address_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
