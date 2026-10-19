"""omodul.add_customer_address — 给买家地址簿加一条地址。

is_default=True 时,同一 customer 下其它地址的 is_default 会被打回 false
(同一时刻只能有一个默认地址)——这一步和插入新地址在同一事务里原子完成。

Composes:
  - obase.persistence.transaction

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(
    config: AddCustomerAddressConfig, input_data: AddCustomerAddressInput
) -> str:
    """Fingerprint over customer_id + 地址字段整体。"""
    return compute_fingerprint(
        {
            "customer_id": input_data.customer_id,
            "recipient_name": input_data.recipient_name,
            "phone": input_data.phone,
            "address_line1": input_data.address_line1,
            "address_line2": input_data.address_line2,
            "city": input_data.city,
            "region_code": input_data.region_code,
            "postal_code": input_data.postal_code,
        }
    )


class AddCustomerAddressConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "add_customer_address"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"customer_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class AddCustomerAddressInput(BaseModel):
    customer_id: str
    recipient_name: str
    phone: str
    address_line1: str
    address_line2: str = ""
    city: str
    region_code: str = ""
    postal_code: str
    is_default: bool = False


async def add_customer_address(
    config: AddCustomerAddressConfig,
    input_data: AddCustomerAddressInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """给买家地址簿加一条地址。

    Args:
        config: AddCustomerAddressConfig。
        input_data: customer_id + 结构化地址字段 + is_default。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 address_id。
    """
    from obase.persistence import transaction
    from obase.uuid7 import uuid7

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — add_customer_address always touches persisted customer"
            )

        fp = compute_fingerprint_for(config, input_data)

        async with transaction(pool) as tx:
            customer = await tx.fetchrow(
                'SELECT id FROM "customer" WHERE id = $1 AND deleted_at IS NULL',
                input_data.customer_id,
            )
            if customer is None:
                raise ValueError(f"customer {input_data.customer_id} not found")

            if input_data.is_default:
                await tx.execute(
                    'UPDATE "customer_address" SET is_default = false, updated_at = NOW() '
                    "WHERE customer_id = $1 AND deleted_at IS NULL",
                    input_data.customer_id,
                )

            address_id = uuid7()
            await tx.execute(
                'INSERT INTO "customer_address" '
                "(id, customer_id, recipient_name, phone, address_line1, address_line2, "
                "city, region_code, postal_code, is_default) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                address_id,
                input_data.customer_id,
                input_data.recipient_name,
                input_data.phone,
                input_data.address_line1,
                input_data.address_line2,
                input_data.city,
                input_data.region_code or None,
                input_data.postal_code,
                input_data.is_default,
            )
            trail.record(event="address_added", address_id=str(address_id))

        if on_step:
            on_step(
                {
                    "stage": "add_customer_address",
                    "status": "done",
                    "customer_id": input_data.customer_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            address_id=address_id,
            customer_id=input_data.customer_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
