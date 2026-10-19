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


def compute_fingerprint_for(config: Config, input_data: InputData) -> str:
    """Fingerprint over root_path + sorted extensions."""
    return compute_fingerprint(
        {
            "root_path": input_data.root_path,
            "extensions": sorted(config.extensions),
        }
    )


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "index_codebase"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"root_path"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "cost"}

    max_concurrent: int = 5
    extensions: list[str] = [".py", ".ts", ".js", ".go", ".rs"]
    incremental: bool = False


class InputData(BaseModel):
    root_path: str
    scanner: Any = None
    embedder: Any = None
    vector_writer: Any = None
    # WAVE G: incremental update mode (None/空 = 全量 rebuild)
    changed_files: list[dict[str, Any]] | None = None
    index_revision: str = ""
    source_revision: str = ""

    model_config = ConfigDict(arbitrary_types_allowed=True)


def _freshness_metadata(
    *, changed: list[dict[str, Any]], prev_index_revision: str
) -> dict[str, str]:
    """freshness metadata: source/index revision + last applied change。"""
    source_revision = compute_fingerprint({"changes": changed})
    index_revision = compute_fingerprint({"prev": prev_index_revision, "source": source_revision})
    last = changed[-1] if changed else {}
    return {
        "source_revision": source_revision,
        "index_revision": index_revision,
        "last_applied_change": (f"{last.get('kind', '')}:{last.get('path', '')}" if last else ""),
    }


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

        # WAVE G incremental mode:只处理受影响文件,等价于全量 rebuild 结果
        changed = list(input_data.changed_files or []) if config.incremental else []
        if changed:
            touched = [
                c.get("path", "")
                for c in changed
                if c.get("kind") in ("added", "modified", "renamed") and c.get("path")
            ]
            removed = [
                (c.get("old_path") or c.get("path", ""))
                for c in changed
                if c.get("kind") in ("deleted", "renamed")
            ]
            inc_results: dict[str, int] = {
                "indexed": 0,
                "skipped": 0,
                "failed": 0,
                "deleted": 0,
            }
            semaphore = asyncio.Semaphore(config.max_concurrent)

            async def embed_one(f: str) -> None:
                async with semaphore:
                    p = Path(f)
                    if not p.exists():
                        inc_results["skipped"] += 1
                        return
                    try:
                        content = p.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        inc_results["skipped"] += 1
                        return
                    if not content.strip():
                        inc_results["skipped"] += 1
                        return
                    embedding: list = []
                    if input_data.embedder is not None:
                        embedding = await _call(input_data.embedder, text=content)
                    if input_data.vector_writer is not None:
                        await _call(
                            input_data.vector_writer,
                            path=f,
                            embedding=embedding,
                            content=content,
                        )
                    inc_results["indexed"] += 1

            gathered = await asyncio.gather(
                *[embed_one(f) for f in touched],
                return_exceptions=True,
            )
            for exc in gathered:
                if isinstance(exc, BaseException) and not isinstance(exc, asyncio.CancelledError):
                    inc_results["failed"] += 1
            for dead in removed:
                try:
                    if input_data.vector_writer is not None:
                        await _call(
                            input_data.vector_writer,
                            path=dead,
                            embedding=[],
                            content="",
                            delete=True,
                        )
                    inc_results["deleted"] += 1
                except Exception:
                    inc_results["failed"] += 1
            freshness = _freshness_metadata(
                changed=changed, prev_index_revision=input_data.index_revision
            )
            result = build_result(
                status="completed",
                error=None,
                fingerprint=fp,
                cost_usd=cost.total_usd,
                **inc_results,
            )
            result.update(freshness)
            return result

        if input_data.scanner is not None:
            files: list[str] = await _call(
                input_data.scanner, root=input_data.root_path, extensions=config.extensions
            )
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

                file_fp = compute_fingerprint({"path": f, "mtime": str(p.stat().st_mtime)})

                embedding: list = []
                if input_data.embedder is not None:
                    embedding = await _call(input_data.embedder, text=content)

                if input_data.vector_writer is not None:
                    await _call(
                        input_data.vector_writer, path=f, embedding=embedding, content=content
                    )

                results["indexed"] += 1

        gathered = await asyncio.gather(
            *[embed_one(f) for f in files],
            return_exceptions=True,
        )
        for exc in gathered:
            if isinstance(exc, BaseException) and not isinstance(exc, asyncio.CancelledError):
                results["failed"] += 1

        freshness = _freshness_metadata(
            changed=[{"kind": "full-rebuild", "path": input_data.root_path}],
            prev_index_revision=input_data.index_revision,
        )
        result = build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            cost_usd=cost.total_usd,
            **results,
        )
        result.update(freshness)
        return result

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
        )
