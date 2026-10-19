"""omodul.update_customer_address — 局部更新一条买家地址。

is_default 若被设为 true,同一 customer 下其它地址的 is_default 打回
false,跟 add_customer_address 同一套互斥逻辑。

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

_UPDATABLE_FIELDS = (
    "recipient_name",
    "phone",
    "address_line1",
    "address_line2",
    "city",
    "region_code",
    "postal_code",
    "is_default",
)


def compute_fingerprint_for(
    config: UpdateCustomerAddressConfig, input_data: UpdateCustomerAddressInput
) -> str:
    """Fingerprint over address_id + 待更新字段快照。"""
    return compute_fingerprint(
        {
            "address_id": input_data.address_id,
            **{f: getattr(input_data, f) for f in _UPDATABLE_FIELDS},
        }
    )


class UpdateCustomerAddressConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_customer_address"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"address_id"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class UpdateCustomerAddressInput(BaseModel):
    address_id: str
    recipient_name: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    region_code: str | None = None
    postal_code: str | None = None
    is_default: bool | None = None


async def update_customer_address(
    config: UpdateCustomerAddressConfig,
    input_data: UpdateCustomerAddressInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新一条买家地址。

    Args:
        config: UpdateCustomerAddressConfig。
        input_data: address_id + 任意子集的可更新字段。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.persistence import transaction

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — update_customer_address always touches persisted address"
            )

        updates = {
            f: getattr(input_data, f)
            for f in _UPDATABLE_FIELDS
            if getattr(input_data, f) is not None
        }
        if not updates:
            raise ValueError("at least one field must be provided to update")

        fp = compute_fingerprint_for(config, input_data)

        async with transaction(pool) as tx:
            address = await tx.fetchrow(
                'SELECT * FROM "customer_address" WHERE id = $1 AND deleted_at IS NULL',
                input_data.address_id,
            )
            if address is None:
                raise ValueError(f"customer_address {input_data.address_id} not found")

            if updates.get("is_default") is True:
                await tx.execute(
                    'UPDATE "customer_address" SET is_default = false, updated_at = NOW() '
                    "WHERE customer_id = $1 AND id != $2 AND deleted_at IS NULL",
                    address["customer_id"],
                    input_data.address_id,
                )

            set_clause = ", ".join(f'"{k}" = ${i + 2}' for i, k in enumerate(updates))
            await tx.execute(
                f'UPDATE "customer_address" SET {set_clause}, updated_at = NOW() WHERE id = $1',
                input_data.address_id,
                *updates.values(),
            )
            trail.record(
                event="address_updated",
                address_id=input_data.address_id,
                fields=list(updates.keys()),
            )

        if on_step:
            on_step(
                {
                    "stage": "update_customer_address",
                    "status": "done",
                    "address_id": input_data.address_id,
                }
            )

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            address_id=input_data.address_id,
            updated_fields=list(updates.keys()),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
