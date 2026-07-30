"""omodul.cancel_fulfillment — 取消尚未发货的履约单据。

只能取消 status='created' 的单据(还没发货)。取消后这批订单行重新计入
"未履约"额度,可被后续 create_fulfillment 再次打包。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CancelFulfillmentConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "cancel_fulfillment"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CancelFulfillmentInput(BaseModel):
    fulfillment_id: str


async def cancel_fulfillment(
    config: CancelFulfillmentConfig,
    input_data: CancelFulfillmentInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """取消一份尚未发货的履约单据。

    Args:
        config: CancelFulfillmentConfig。
        input_data: fulfillment_id。
        output_dir: decision_trail 落盘目录。
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
                "pool is required — cancel_fulfillment always touches persisted fulfillment"
            )

        async with transaction(pool) as tx:
            fulfillment = await tx.fetchrow(
                'SELECT * FROM "fulfillment" WHERE id = $1 FOR UPDATE', input_data.fulfillment_id
            )
            if fulfillment is None:
                raise ValueError(f"fulfillment {input_data.fulfillment_id} not found")
            if fulfillment["status"] != "created":
                raise ValueError(
                    f"fulfillment {input_data.fulfillment_id} cannot be canceled from status "
                    f"{fulfillment['status']!r}"
                )

            await tx.execute(
                "UPDATE \"fulfillment\" SET status = 'canceled', updated_at = NOW() WHERE id = $1",
                input_data.fulfillment_id,
            )
            trail.record(event="fulfillment_canceled", fulfillment_id=input_data.fulfillment_id)

        if on_step:
            on_step(
                {
                    "stage": "cancel_fulfillment",
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
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
