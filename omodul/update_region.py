"""omodul.update_region — 局部更新区域字段。

payment_provider_names 若被更新,同 create_region 校验每个名字都已注册。

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint

_UPDATABLE_FIELDS = ("name", "currency", "payment_provider_names", "status")


def compute_fingerprint_for(config: UpdateRegionConfig, input_data: UpdateRegionInput) -> str:
    """Fingerprint over code + 待更新字段快照。"""
    return compute_fingerprint(
        {"code": input_data.code, **{f: getattr(input_data, f) for f in _UPDATABLE_FIELDS}}
    )


class UpdateRegionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "update_region"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"code"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class UpdateRegionInput(BaseModel):
    code: str
    name: str | None = None
    currency: str | None = None
    payment_provider_names: list[str] | None = None
    status: str | None = None


async def update_region(
    config: UpdateRegionConfig,
    input_data: UpdateRegionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """局部更新区域。payment_provider_names 若提供,逐个校验已注册。

    Args:
        config: UpdateRegionConfig。
        input_data: code + 任意子集的可更新字段。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool。本函数必须落库,pool 为 None 直接判 failed。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict。
    """
    from obase.exceptions import ProviderNotFoundError
    from obase.persistence import read_one, update_one
    from obase.provider_registry import ProviderRegistry

    trail = Trail()

    try:
        if pool is None:
            raise ValueError("pool is required — update_region always touches persisted region")

        updates = {
            f: getattr(input_data, f)
            for f in _UPDATABLE_FIELDS
            if getattr(input_data, f) is not None
        }
        if not updates:
            raise ValueError("at least one field must be provided to update")

        if input_data.payment_provider_names is not None:
            for provider_name in input_data.payment_provider_names:
                try:
                    ProviderRegistry.get().generic("payment", provider_name)
                except ProviderNotFoundError as exc:
                    raise ValueError(
                        f"payment provider {provider_name!r} is not registered"
                    ) from exc

        fp = compute_fingerprint_for(config, input_data)

        region = await read_one(pool, table="region", id=input_data.code, id_column="code")
        if region is None:
            raise ValueError(f"region {input_data.code!r} not found")

        await update_one(pool, table="region", id=input_data.code, data=updates, id_column="code")
        trail.record(event="region_updated", code=input_data.code, fields=list(updates.keys()))

        if on_step:
            on_step({"stage": "update_region", "status": "done", "code": input_data.code})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            code=input_data.code,
            updated_fields=list(updates.keys()),
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
