"""
omodul.process_prompt — LLM prompt processing with tool-call parsing.

Pillars: decision_trail, cost
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import (
    BaseConfig, CostTracker, Trail, build_result, compute_fingerprint,
    extract_text, write_report,
)

_current_cost_m01: ContextVar[CostTracker] = ContextVar("_current_cost_m01")


async def _call(fn: Any, **kwargs: Any) -> Any:
    import inspect
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "process_prompt"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}

    max_tokens: int = 4096
    streaming: bool = False


class InputData(BaseModel):
    messages: list[dict]
    tools: list[dict] = []
    system: str = ""
    llm_caller: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def process_prompt(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Process an LLM prompt, handle tool calls, record trail and cost."""
    cost = CostTracker()
    _current_cost_m01.set(cost)
    trail = Trail()

    try:
        if input_data.llm_caller is None:
            return build_result(
                status="failed",
                error={"type": "ConfigError", "message": "llm_caller is required"},
                trail=trail,
                cost_usd=cost.total_usd,
            )

        trail.record(event="assemble_context", step_no=0,
                     n_messages=len(input_data.messages),
                     n_tools=len(input_data.tools))

        if on_step:
            await _call(on_step, step="assemble_context")

        response = await _call(
            input_data.llm_caller,
            messages=input_data.messages,
            tools=input_data.tools or None,
            max_tokens=config.max_tokens,
        )

        cost.add_from_response(response, model=config.llm_model)
        trail.record(event="llm_response", step_no=1,
                     in_tokens=response.get("usage", {}).get("input_tokens", 0),
                     out_tokens=response.get("usage", {}).get("output_tokens", 0))

        # Parse tool calls from response content
        content = response.get("content", [])
        if isinstance(content, str):
            content = []
        tool_calls = [
            b for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        assistant_message = {"role": "assistant", "content": content}

        loop = asyncio.get_event_loop()
        await asyncio.shield(loop.run_in_executor(None, trail.write, output_dir))

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            cost_usd=cost.total_usd,
            assistant_message=assistant_message,
            tool_calls=tool_calls,
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            cost_usd=cost.total_usd,
        )
