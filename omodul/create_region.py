"""omodul.create_region — 新建区域,校验关联支付方式已注册。

主键是 code(如 "cn-east"),不是自动生成的 UUID——跟 cart/stock_location/
customer_order 里到处用的 region_code 是同一个自然键,调用方直接传自己
选定的 code。

Composes:
  - obase.provider_registry.ProviderRegistry(校验 payment_provider_names
    都已经在 "payment" category 下注册,不允许挂一个不存在的支付方式)
  - obase.persistence.insert_one

Pillars: fingerprint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


def compute_fingerprint_for(config: CreateRegionConfig, input_data: CreateRegionInput) -> str:
    """Fingerprint over code。"""
    return compute_fingerprint({"code": input_data.code})


class CreateRegionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "create_region"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"code"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}


class CreateRegionInput(BaseModel):
    code: str
    name: str
    currency: str
    payment_provider_names: list[str] = []


async def create_region(
    config: CreateRegionConfig,
    input_data: CreateRegionInput,
    output_dir: Path,
    *,
    pool: Any = None,
    on_step: Any = None,
) -> dict:
    """新建区域。payment_provider_names 里每个名字都必须已经在
    ProviderRegistry 的 "payment" category 下注册,否则拒绝(不允许挂一个
    调用方拼错名字、或者压根没接的支付方式)。

    Args:
        config: CreateRegionConfig。
        input_data: code / name / currency / payment_provider_names。
        output_dir: decision_trail 落盘目录(本元素未启用 decision_trail pillar)。
        pool: obase.persistence.PgPool;为 None 时跳过持久化(dry-run,便于单测)。
        on_step: 进度回调,可选。

    Returns:
        标准 omodul 返回 dict,findings 中含 code。
    """
    from obase.exceptions import ProviderNotFoundError
    from obase.persistence import insert_one
    from obase.provider_registry import ProviderRegistry

    trail = Trail()

    try:
        if not input_data.code:
            raise ValueError("code is required")
        if not input_data.name:
            raise ValueError("name is required")
        if not input_data.currency:
            raise ValueError("currency is required")

        for provider_name in input_data.payment_provider_names:
            try:
                ProviderRegistry.get().generic("payment", provider_name)
            except ProviderNotFoundError as exc:
                raise ValueError(f"payment provider {provider_name!r} is not registered") from exc

        fp = compute_fingerprint_for(config, input_data)

        row = {
            "code": input_data.code,
            "name": input_data.name,
            "currency": input_data.currency,
            "payment_provider_names": input_data.payment_provider_names,
        }

        if pool is not None:
            await insert_one(pool, table="region", data=row, returning="code")
            trail.record(event="persisted", code=input_data.code)
        else:
            trail.record(event="persisted_skipped_no_pool", code=input_data.code)

        if on_step:
            on_step({"stage": "create_region", "status": "done", "code": input_data.code})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            code=input_data.code,
            name=input_data.name,
            currency=input_data.currency,
        )

    except Exception as exc:
        trail.record(event="error", detail=str(exc))
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
