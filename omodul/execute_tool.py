"""
omodul.execute_tool — Permission-gated tool execution with sync/async dispatch.

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
    _omodul_name: ClassVar[str] = "execute_tool"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class InputData(BaseModel):
    tool_name: str
    tool_args: dict = {}
    tool_call_id: str = ""
    permission_checker: Any = None
    tool_registry: dict = {}

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def execute_tool(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Execute a named tool after permission check; supports sync and async callables."""
    trail = Trail()

    try:
        trail.record(event="permission_check", step_no=0,
                     tool_name=input_data.tool_name)

        if input_data.permission_checker is not None:
            perm = await _call(
                input_data.permission_checker,
                tool_name=input_data.tool_name,
                args=input_data.tool_args,
            )
            if perm == "deny":
                return build_result(
                    status="failed",
                    error={"type": "PermissionDenied",
                           "message": f"tool '{input_data.tool_name}' was denied"},
                    trail=trail,
                )
            if perm == "ask":
                return build_result(
                    status="completed",
                    error=None,
                    trail=trail,
                    needs_confirmation=True,
                    tool_name=input_data.tool_name,
                    tool_args=input_data.tool_args,
                )

        trail.record(event="execute_tool", step_no=1)

        tool_fn = input_data.tool_registry.get(input_data.tool_name)
        if tool_fn is None:
            return build_result(
                status="failed",
                error={"type": "ToolNotFound",
                       "message": f"tool '{input_data.tool_name}' not in registry"},
                trail=trail,
            )

        raw = tool_fn(**input_data.tool_args)
        if inspect.isawaitable(raw):
            raw = await raw

        # Normalise result: may be a dict with exit_code or a bare value
        if isinstance(raw, dict):
            tool_result = raw.get("output", raw)
            exit_code = raw.get("exit_code", 0)
        else:
            tool_result = raw
            exit_code = 0

        trail.record(event="tool_result", step_no=2,
                     exit_code=exit_code,
                     tool_call_id=input_data.tool_call_id)

        loop = asyncio.get_event_loop()
        await asyncio.shield(loop.run_in_executor(None, trail.write, output_dir))

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            tool_result=tool_result,
            exit_code=exit_code,
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
        )
