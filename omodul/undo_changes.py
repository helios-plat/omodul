"""
omodul.undo_changes — Restore a project snapshot by ID.

Pillars: decision_trail
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result


async def _call(fn: Any, **kwargs: Any) -> Any:
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "undo_changes"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class InputData(BaseModel):
    snap_id: str
    cwd: str
    snapshot_lister: Any = None
    snapshot_restorer: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def undo_changes(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Find a snapshot by ID and restore it; records each stage in the trail."""
    trail = Trail()

    try:
        trail.record(event="find_snapshot", step_no=0,
                     snap_id=input_data.snap_id, cwd=input_data.cwd)

        if input_data.snapshot_lister is not None:
            snaps = await _call(input_data.snapshot_lister, cwd=input_data.cwd)
            snap_ids = [s.get("id") for s in (snaps or [])]
            if input_data.snap_id not in snap_ids:
                loop = asyncio.get_event_loop()
                await asyncio.shield(
                    loop.run_in_executor(None, trail.write, output_dir)
                )
                return build_result(
                    status="failed",
                    error={"type": "SnapshotNotFound",
                           "message": f"snapshot '{input_data.snap_id}' not found"},
                    trail=trail,
                )

        trail.record(event="restore_snapshot", step_no=1)

        if input_data.snapshot_restorer is not None:
            await _call(input_data.snapshot_restorer,
                        snap_id=input_data.snap_id,
                        cwd=input_data.cwd)

        trail.record(event="restored", step_no=2, snap_id=input_data.snap_id)

        loop = asyncio.get_event_loop()
        await asyncio.shield(loop.run_in_executor(None, trail.write, output_dir))

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            snap_id=input_data.snap_id,
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
        )
