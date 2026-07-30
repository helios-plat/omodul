"""omodul.ship_fulfillment — 生成运单并把履约单据标记为已发货。

Composes:
  - obase.persistence.transaction
  - oprim.ext_ship_create_label

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class ShipFulfillmentConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "ship_fulfillment"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class ShipFulfillmentInput(BaseModel):
    fulfillment_id: str
    provider_name: str
    shipment_info: dict[str, Any]


async def ship_fulfillment(
    config: ShipFulfillmentConfig,
    input_data: ShipFulfillmentInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """对一份 created 状态的履约单据生成运单,更新追踪号并转 shipped。

    Args:
        config: ShipFulfillmentConfig。
        input_data: fulfillment_id / provider_name / shipment_info(透传给
            oprim.ext_ship_create_label 的 shipment_info)。
        output_dir: decision_trail 落盘目录。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict;findings 含 tracking_number/carrier。
    """
    from obase.persistence import transaction
    from oprim.ext_ship_create_label import ext_ship_create_label

    trail = Trail()

    try:
        if pool is None:
            raise ValueError(
                "pool is required — ship_fulfillment always touches persisted fulfillment"
            )

        async with transaction(pool) as tx:
            fulfillment = await tx.fetchrow(
                'SELECT * FROM "fulfillment" WHERE id = $1 FOR UPDATE', input_data.fulfillment_id
            )
            if fulfillment is None:
                raise ValueError(f"fulfillment {input_data.fulfillment_id} not found")
            if fulfillment["status"] != "created":
                raise ValueError(
                    f"fulfillment {input_data.fulfillment_id} cannot be shipped from status "
                    f"{fulfillment['status']!r}"
                )

            label = await ext_ship_create_label(
                input_data.provider_name, shipment_info=input_data.shipment_info
            )
            trail.record(
                event="label_created",
                provider_name=input_data.provider_name,
                tracking_number=label.get("tracking_number"),
            )

            await tx.execute(
                "UPDATE \"fulfillment\" SET status = 'shipped', provider_name = $1, "
                "tracking_number = $2, carrier = $3, updated_at = NOW() WHERE id = $4",
                input_data.provider_name,
                label.get("tracking_number"),
                label.get("carrier"),
                input_data.fulfillment_id,
            )
            trail.record(event="fulfillment_shipped", fulfillment_id=input_data.fulfillment_id)

        if on_step:
            on_step(
                {
                    "stage": "ship_fulfillment",
                    "status": "done",
                    "fulfillment_id": input_data.fulfillment_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            fulfillment_id=input_data.fulfillment_id,
            tracking_number=label.get("tracking_number"),
            carrier=label.get("carrier"),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
