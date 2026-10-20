"""omodul.mcp_tool_export_workflow — MCP 工具导出事务.

集成：oskill.mcp_schema_adapter（Schema 转换）+ oprim.mcp_register_tool
（工具注册）+ obase.mcp_server（注册/清单）+ cost 记账（描述生成）。

Pillars: decision_trail, cost
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, CostTracker, Trail, build_result


class McpToolExportConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "mcp_tool_export_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}

    server_name: str = "veya"
    server_version: str = "1.0.0"
    auto_create_server: bool = True   # server 未注入时自动创建 MCPServer


class McpToolExportInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    tools: list[dict] = []   # [{name, description, input_schema, handler, output_schema?}]
    server: Any = None       # obase.mcp_server.MCPServer 注入（None → 自动创建）
    caller: Any = None       # LLM（可选：description 为空时生成）


async def mcp_tool_export_workflow(
    config: McpToolExportConfig,
    input_data: McpToolExportInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """批量导出工具为 MCP 注册。失败绝不 raise。"""
    trail = Trail()
    cost = CostTracker()

    try:
        if not input_data.tools:
            raise ValueError("tools must not be empty")

        server = input_data.server
        if server is None:
            if not config.auto_create_server:
                raise ValueError("server is required (auto_create_server=False)")
            from obase.mcp_server import MCPServer

            server = MCPServer(name=config.server_name, version=config.server_version)

        from oprim import mcp_register_tool
        from oskill import mcp_schema_adapter

        registered: list[dict[str, Any]] = []
        for i, tool in enumerate(input_data.tools):
            name = tool.get("name", "")
            if not name:
                raise ValueError(f"tool #{i} missing name")

            description = tool.get("description", "")
            if not description and input_data.caller is not None:
                # LLM 生成描述（cost 记账）
                response = await _call_llm(input_data.caller, tool, config)
                cost.add_from_response(response, model=config.llm_model)
                description = _extract_text(response)
                trail.record(event="description_generated", tool=name)

            input_schema = tool.get("input_schema")
            if not isinstance(input_schema, dict):
                raise ValueError(f"tool {name!r} missing input_schema")

            # Schema 规范化（to_mcp: camelCase）
            schema = mcp_schema_adapter(input_schema, direction="to_mcp")

            result = mcp_register_tool(
                server,
                name=name,
                description=description or name,
                input_schema=schema,
                handler=tool["handler"],
                output_schema=tool.get("output_schema"),
            )
            registered.append(result)
            trail.record(event="tool_registered", tool=name)
            if on_step:
                on_step({"stage": "register", "tool": name, "step": i})

        listing = server.list_tools() if hasattr(server, "list_tools") else registered

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            cost_usd=round(cost.total_usd, 6),
            findings={
                "registered": len(registered),
                "tools": listing,
                "server": getattr(server, "name", config.server_name),
            },
        )

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            cost_usd=round(cost.total_usd, 6),
        )


async def _call_llm(caller: Any, tool: dict, config: Any) -> dict:
    return await caller(
        messages=[
            {
                "role": "user",
                "content": (
                    f"为 MCP 工具 {tool['name']!r} 写一句英文描述（≤40 词），"
                    f"输入参数: {tool.get('input_schema', {})}"
                ),
            }
        ],
        tools=None,
        max_tokens=128,
    )


def _extract_text(response: dict) -> str:
    content = response.get("content", "")
    if isinstance(content, str):
        return content.strip()
    return "".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    ).strip()
