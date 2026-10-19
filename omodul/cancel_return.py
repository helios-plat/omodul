"""omodul.cancel_return — 取消尚未收货的退货申请。

只能取消 status='requested' 的申请(还没收货/退款)。取消后该订单行重新
计入"未申请退货"额度。

Pillars: decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result


class CancelReturnConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "cancel_return"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class CancelReturnInput(BaseModel):
    return_request_id: str


async def cancel_return(
    config: CancelReturnConfig,
    input_data: CancelReturnInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """取消一份尚未收货的退货申请。

    Args:
        config: CancelReturnConfig。
        input_data: return_request_id。
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
                "pool is required — cancel_return always touches persisted return_request"
            )

        async with transaction(pool) as tx:
            ret = await tx.fetchrow(
                'SELECT * FROM "return_request" WHERE id = $1 FOR UPDATE',
                input_data.return_request_id,
            )
            if ret is None:
                raise ValueError(f"return_request {input_data.return_request_id} not found")
            if ret["status"] != "requested":
                raise ValueError(
                    f"return_request {input_data.return_request_id} cannot be canceled from "
                    f"status {ret['status']!r}"
                )

            await tx.execute(
                "UPDATE \"return_request\" SET status = 'canceled', updated_at = NOW() "
                "WHERE id = $1",
                input_data.return_request_id,
            )
            trail.record(
                event="return_request_canceled", return_request_id=input_data.return_request_id
            )

        if on_step:
            on_step(
                {
                    "stage": "cancel_return",
                    "status": "done",
                    "return_request_id": input_data.return_request_id,
                }
            )

        trail_path = trail.write(Path(output_dir)) if output_dir else None

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail_path,
            return_request_id=input_data.return_request_id,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
