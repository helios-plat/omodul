"""
omodul.web_research_task — Web research with optional LLM synthesis and report output.

Pillars: report, cost
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import (
    BaseConfig, CostTracker, build_result, extract_text, write_report,
)

_current_cost_m10: ContextVar[CostTracker] = ContextVar("_current_cost_m10")


async def _call(fn: Any, **kwargs: Any) -> Any:
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "web_research_task"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"report", "cost"}

    max_pages: int = 5


class InputData(BaseModel):
    query: str
    researcher: Any = None
    llm_caller: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def web_research_task(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Fetch web snippets for a query, optionally synthesise with LLM, write report."""
    cost = CostTracker()
    _current_cost_m10.set(cost)

    try:
        if input_data.researcher is not None:
            result = await _call(input_data.researcher,
                                 query=input_data.query,
                                 max_pages=config.max_pages)
        else:
            result = {"snippets": [], "urls": []}

        if not result.get("snippets"):
            return build_result(
                status="failed",
                error={"type": "NoResults", "message": "no results found"},
                cost_usd=cost.total_usd,
            )

        if input_data.llm_caller is not None:
            joined = "\n\n".join(result["snippets"])
            messages = [{"role": "user",
                         "content": f"Synthesise the following research snippets into a concise summary:\n\n{joined}"}]
            response = await _call(input_data.llm_caller,
                                   messages=messages,
                                   max_tokens=2048)
            cost.add_from_response(response, model=config.llm_model)
            summary = extract_text(response) or joined
        else:
            summary = "\n".join(result.get("snippets", []))

        report_path = write_report(
            summary,
            output_dir=output_dir,
            name=f"research_{uuid.uuid4().hex[:8]}",
            fmt="markdown",
        )

        return build_result(
            status="completed",
            error=None,
            cost_usd=cost.total_usd,
            report_path=str(report_path),
        )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            cost_usd=cost.total_usd,
        )
