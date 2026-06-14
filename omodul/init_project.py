"""
omodul.init_project — Scan a project and generate AGENTS.md via LLM analysis.

Pillars: report, cost
Correction 1: async def — scan_project_structure is async.
"""
from __future__ import annotations

import asyncio
import inspect
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import (
    BaseConfig, CostTracker, Trail, build_result, extract_text, write_report,
)

_current_cost_m04: ContextVar[CostTracker] = ContextVar("_current_cost_m04")


async def _call(fn: Any, **kwargs: Any) -> Any:
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class Config(BaseConfig):
    _omodul_name: ClassVar[str] = "init_project"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"report", "cost"}

    max_files: int = 500


class InputData(BaseModel):
    root_path: str
    scan_fn: Any = None
    llm_caller: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


async def init_project(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """Scan a project structure and generate an AGENTS.md with LLM analysis."""
    cost = CostTracker()
    _current_cost_m04.set(cost)
    trail = Trail()

    try:
        root = Path(input_data.root_path)

        trail.record(event="scan_project", step_no=0, root=str(root))

        if input_data.scan_fn is not None:
            scan_result = await _call(input_data.scan_fn, root=root)
        else:
            # Fallback: list files up to max_files
            files = list(root.rglob("*"))[:config.max_files]
            scan_result = {
                "files": [str(f.relative_to(root)) for f in files if f.is_file()],
                "root": str(root),
            }

        trail.record(event="llm_analyze", step_no=1,
                     n_files=len(scan_result.get("files", [])))

        if input_data.llm_caller is not None:
            files_list = "\n".join(scan_result.get("files", []))
            messages = [
                {
                    "role": "user",
                    "content": (
                        f"Analyse this project structure and write an AGENTS.md "
                        f"that describes the repo layout, key modules, and agent entry points.\n\n"
                        f"Root: {scan_result.get('root', input_data.root_path)}\n"
                        f"Files:\n{files_list}"
                    ),
                }
            ]
            response = await _call(
                input_data.llm_caller,
                messages=messages,
                max_tokens=4096,
            )
            cost.add_from_response(response, model=config.llm_model)
            agents_md_content = extract_text(response) or "# AGENTS\n\n_No content generated._"
        else:
            agents_md_content = (
                f"# AGENTS\n\nProject root: `{input_data.root_path}`\n\n"
                f"Files scanned: {len(scan_result.get('files', []))}\n"
            )

        report_path = write_report(
            agents_md_content,
            output_dir=output_dir,
            name="AGENTS",
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
