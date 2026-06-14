"""
omodul.sync_models_catalog — Fetch and filter a models catalog by curated providers.

Pillars: fingerprint
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, build_result, compute_fingerprint


async def _call(fn: Any, **kwargs: Any) -> Any:
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def compute_fingerprint_for(config: "Config", input_data: "InputData") -> str:
    """Fingerprint over catalog_version + sorted curated_providers."""
    return compute_fingerprint({
        "catalog_version": config.catalog_version,
        "curated_providers": sorted(config.curated_providers),
    })


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "sync_models_catalog"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"catalog_version"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint"}

    catalog_version: str = ""
    curated_providers: list[str] = ["anthropic", "openai", "google"]


class InputData(BaseModel):
    fetcher: Any = None
    refresh: bool = False

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def sync_models_catalog(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Fetch a models catalog and filter to curated providers."""
    try:
        fp = compute_fingerprint_for(config, input_data)

        if input_data.fetcher is not None:
            models = await _call(input_data.fetcher, refresh=input_data.refresh)
        else:
            models = []

        def _provider(m: Any) -> str:
            if isinstance(m, dict):
                return m.get("provider", "")
            return getattr(m, "provider", "")

        curated = [m for m in models if _provider(m) in config.curated_providers]

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            total_fetched=len(models),
            curated_count=len(curated),
            models=curated,
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
        )
