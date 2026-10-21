"""omodul.mcp_execution_workflow — 远程 MCP 工具链组装与安全调度事务.

集成：oskill.mcp_tool_route（自然语言路由）+ oprim.mcp_call_tool（工具调用）
+ 白名单校验 + cost 记账。

Pillars: decision_trail, cost
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, CostTracker, Trail, build_result


class McpExecutionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "mcp_execution_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}

    max_tool_calls: int = 10
    allowed_tools: list[str] = []  # 空 = 全放行；非空 = 白名单


class McpExecutionInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: str                       # 自然语言任务
    client: Any = None              # McpClientHandle（list_tools/call_tool）
    tools: list[dict] = []          # 候选工具清单 [{name, description, ...}]
    embedder: Any = None            # 路由向量化器（可选）
    store: Any = None               # 路由检索存储（可选）
    explicit_plan: list[dict] = []  # 显式工具链 [{"tool": str, "arguments": dict}]


async def mcp_execution_workflow(
    config: McpExecutionConfig,
    input_data: McpExecutionInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """组装 MCP 工具链并安全调度执行。失败绝不 raise。"""
    trail = Trail()
    cost = CostTracker()

    try:
        if input_data.client is None:
            raise ValueError("client (McpClientHandle) is required")
        if len(input_data.explicit_plan) > config.max_tool_calls:
            raise ValueError(
                f"explicit_plan exceeds max_tool_calls={config.max_tool_calls}"
            )

        from oprim import mcp_call_tool

        # 1) 工具清单：显式提供或从 client 拉取
        tool_list = list(input_data.tools)
        if not tool_list:
            tool_list = await input_data.client.list_tools()
            tool_list = [
                {"name": t.get("name", ""), "description": t.get("description", "")}
                for t in tool_list
            ]
        if not tool_list:
            raise ValueError("no MCP tools available")

        # 2) 组装执行计划：显式 plan 或 LLM 路由
        if input_data.explicit_plan:
            plan = input_data.explicit_plan
            trail.record(event="plan_explicit", steps=len(plan))
        else:
            if input_data.embedder is None or input_data.store is None:
                raise ValueError(
                    "auto-routing requires embedder + store; or pass explicit_plan"
                )
            from oskill import mcp_tool_route

            route = await mcp_tool_route(
                input_data.task,
                tools=tool_list,
                embedder=input_data.embedder,
                store=input_data.store,
                top_k=1,
            )
            best = route["best_tool"]
            plan = [{"tool": best["name"], "arguments": {}}]
            trail.record(
                event="tool_routed", tool=best["name"], score=best.get("score"),
            )

        # 3) 白名单 + 顺序执行
        results: list[dict[str, Any]] = []
        for i, step in enumerate(plan):
            tool_name = step.get("tool", "")
            if not tool_name:
                raise ValueError(f"plan step {i} missing tool name")
            if config.allowed_tools and tool_name not in config.allowed_tools:
                raise ValueError(f"tool {tool_name!r} not in allowed_tools whitelist")

            if on_step:
                on_step({"stage": "call", "tool": tool_name, "step": i})
            result = await mcp_call_tool(
                tool_name,
                arguments=step.get("arguments", {}),
                client=input_data.client,
            )
            results.append({"tool": tool_name, "result": result})
            trail.record(event="tool_called", tool=tool_name, is_error=result.get("isError", False))

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            cost_usd=round(cost.total_usd, 6),
            calls=len(results),
            results=results,
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
