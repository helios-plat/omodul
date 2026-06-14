"""
omodul.index_codebase — Scan, embed, and vector-write codebase files concurrently.

Pillars: fingerprint, cost
"""
from __future__ import annotations

import asyncio
import inspect
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, CostTracker, build_result, compute_fingerprint

_current_cost_m13: ContextVar[CostTracker] = ContextVar("_current_cost_m13")


async def _call(fn: Any, **kwargs: Any) -> Any:
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def compute_fingerprint_for(config: "Config", input_data: "InputData") -> str:
    """Fingerprint over root_path + sorted extensions."""
    return compute_fingerprint({
        "root_path": input_data.root_path,
        "extensions": sorted(config.extensions),
    })


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "index_codebase"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"root_path"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "cost"}

    max_concurrent: int = 5
    extensions: list[str] = [".py", ".ts", ".js", ".go", ".rs"]


class InputData(BaseModel):
    root_path: str
    scanner: Any = None
    embedder: Any = None
    vector_writer: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def index_codebase(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Scan a codebase, embed each file, and write vectors; runs max_concurrent at once."""
    cost = CostTracker()
    _current_cost_m13.set(cost)

    try:
        fp = compute_fingerprint_for(config, input_data)

        if input_data.scanner is not None:
            files: list[str] = await _call(input_data.scanner,
                                           root=input_data.root_path,
                                           extensions=config.extensions)
        else:
            files = []

        results: dict[str, int] = {"indexed": 0, "skipped": 0, "failed": 0}
        semaphore = asyncio.Semaphore(config.max_concurrent)

        async def embed_one(f: str) -> None:
            async with semaphore:
                p = Path(f)
                if not p.exists():
                    results["skipped"] += 1
                    return
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    results["skipped"] += 1
                    return

                if not content.strip():
                    results["skipped"] += 1
                    return

                file_fp = compute_fingerprint(
                    {"path": f, "mtime": str(p.stat().st_mtime)}
                )

                embedding: list = []
                if input_data.embedder is not None:
                    embedding = await _call(input_data.embedder, text=content)

                if input_data.vector_writer is not None:
                    await _call(input_data.vector_writer,
                                path=f,
                                embedding=embedding,
                                content=content)

                results["indexed"] += 1

        gathered = await asyncio.gather(
            *[embed_one(f) for f in files],
            return_exceptions=True,
        )
        for exc in gathered:
            if isinstance(exc, BaseException) and not isinstance(exc, asyncio.CancelledError):
                results["failed"] += 1

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            cost_usd=cost.total_usd,
            **results,
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
        )
