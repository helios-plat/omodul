"""3O 范式全栈 — omodul 业务事务: 工具调用五步管线 (元素 22).

提取 -> 校验 -> 执行: 组合 oskill 纯解析与 oprim 物理执行。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from obase import OmodulBaseConfig, VFSProvider
from oprim import execute_sandbox_cmd
from oskill import parse_tool_call_json, validate_tool_args
from pydantic import BaseModel


class ToolConfig(OmodulBaseConfig):
    _omodul_name = "tool_pipeline"
    _omodul_version = "1.0.0"
    _enabled_pillars = {"decision_trail"}


class ToolInput(BaseModel):
    raw_llm_text: str
    allowed_schemas: dict[str, type[BaseModel]]


async def _write_trail_async(output_dir: Path, trail: list) -> None:
    """辅助函数：将审计轨迹落盘"""
    trail_path = output_dir / "decision_trail.json"
    with open(trail_path, "w") as f:
        json.dump(trail, f, indent=2)


async def run_tool_pipeline(
    config: ToolConfig,
    input_data: ToolInput,
    output_dir: Path,
    *,
    vfs: VFSProvider,  # 依赖注入物理执行器
    on_step: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """五步管线：提取 -> 校验 -> 执行 (组合 oskill 与 oprim)

    Composes oskill: [parse_tool_call_json, validate_tool_args]
    Composes oprim: [execute_sandbox_cmd]
    """
    decision_trail = []
    status = "completed"
    error = None
    findings = {}

    try:
        if on_step:
            on_step({"step": 1, "msg": "parse tool call"})

        # 1. 组合 oskill: 解析
        parsed = parse_tool_call_json(input_data.raw_llm_text)
        tool_name = parsed.get("tool_name")
        args = parsed.get("args", {})

        if tool_name not in input_data.allowed_schemas:
            raise ValueError(f"Tool {tool_name} not permitted.")

        # 2. 组合 oskill: 校验
        validated = validate_tool_args(input_data.allowed_schemas[tool_name], args)
        decision_trail.append({"action": "validate", "tool": tool_name})

        # 3. 组合 oprim: 执行 (以 shell 为例)
        if tool_name == "shell_exec":
            findings = await execute_sandbox_cmd(
                validated.model_dump().get("command", ""), vfs=vfs
            )
        decision_trail.append({"action": "execute", "tool": tool_name, "status": "success"})

        if on_step:
            on_step({"step": 2, "msg": "pipeline done"})

    except asyncio.CancelledError:
        status, error = "cancelled", {"reason": "timeout"}
        decision_trail.append({"action": "cancelled"})
        raise

    except Exception as e:
        status, error = "failed", {"message": str(e)}
        decision_trail.append({"error": error})

    finally:
        # 轨迹落盘 (shield 防取消打断); return 刻意置于 finally 之外,
        # 避免 finally-return 吞掉重新抛出的 CancelledError。
        if "decision_trail" in config._enabled_pillars:
            output_dir.mkdir(parents=True, exist_ok=True)
            await asyncio.shield(_write_trail_async(output_dir, decision_trail))

    return {"status": status, "error": error, "findings": findings}
