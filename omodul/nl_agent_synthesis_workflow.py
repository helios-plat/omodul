"""omodul.nl_agent_synthesis_workflow — 自然语言 Agent 合成事务.

集成：oprim.agent_prompt_synthesize（LLM 提示词合成）+ 工具清单装配
（oskill.mcp_schema_adapter 规范化）+ 产出报告。

Pillars: fingerprint, decision_trail, report, cost
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import (
    BaseConfig,
    CostTracker,
    Trail,
    build_result,
    compute_fingerprint,
    write_report,
)


class NlAgentSynthesisConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "nl_agent_synthesis_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"goal"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report", "cost"}

    agent_name: str = "synthesized-agent"
    report_format: str = "markdown"


class NlAgentSynthesisInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    goal: str
    context: str = ""
    persona: str = ""
    capabilities: list[str] = []
    tools: list[dict] = []   # [{"name", "description", "input_schema"}]
    caller: Any = None       # LLM 注入


def compute_fingerprint_for(
    config: NlAgentSynthesisConfig, input_data: NlAgentSynthesisInput
) -> str:
    """Fingerprint over goal + agent_name。"""
    return compute_fingerprint(
        {"goal": input_data.goal, "agent": config.agent_name}
    )


async def nl_agent_synthesis_workflow(
    config: NlAgentSynthesisConfig,
    input_data: NlAgentSynthesisInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """合成 Agent（提示词 + 工具清单 + 报告）。失败绝不 raise。"""
    trail = Trail()
    cost = CostTracker()
    fp = compute_fingerprint_for(config, input_data)
    t0 = time.time()

    try:
        if not input_data.goal.strip():
            raise ValueError("goal must not be empty")
        if input_data.caller is None:
            raise ValueError("caller (LLM) must be injected")

        if on_step:
            on_step({"stage": "synthesize", "status": "running"})
        from oprim import agent_prompt_synthesize

        result = await agent_prompt_synthesize(
            input_data.goal,
            caller=input_data.caller,
            context=input_data.context,
            persona=input_data.persona,
            capabilities=input_data.capabilities,
        )
        usage = result.get("usage", {})
        cost.add(
            in_tok=int(usage.get("input_tokens", 0)),
            out_tok=int(usage.get("output_tokens", 0)),
            model=config.llm_model,
        )
        prompt_text = result["prompt"]
        trail.record(event="prompt_synthesized", chars=len(prompt_text))

        # 工具清单规范化（camelCase schema）
        from oskill import mcp_schema_adapter

        tools: list[dict[str, Any]] = []
        for t in input_data.tools:
            schema = t.get("input_schema")
            if isinstance(schema, dict) and schema.get("type") == "object":
                schema = mcp_schema_adapter(schema, direction="to_mcp")
            tools.append(
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "input_schema": schema,
                }
            )
        trail.record(event="tools_assembled", count=len(tools))

        manifest = {
            "name": config.agent_name,
            "goal": input_data.goal,
            "prompt": prompt_text,
            "tools": tools,
            "capabilities": input_data.capabilities,
        }

        report = _render_report(manifest, cost)
        report_path = write_report(
            report, output_dir=Path(output_dir), name="nl_agent_synthesis",
            fmt=config.report_format,
        )
        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            report_path=report_path,
            cost_usd=round(cost.total_usd, 6),
            findings=manifest,
            elapsed_s=round(time.time() - t0, 2),
        )

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            cost_usd=round(cost.total_usd, 6),
        )


def _render_report(manifest: dict, cost: CostTracker) -> str:
    tools = "\n".join(
        f"- `{t['name']}`: {t['description']}" for t in manifest["tools"]
    ) or "- (无)"
    return (
        f"# Agent 合成报告: {manifest['name']}\n\n"
        f"- 目标: {manifest['goal']}\n"
        f"- 成本: ${cost.total_usd:.6f}\n\n"
        f"## System Prompt\n```\n{manifest['prompt']}\n```\n\n"
        f"## 工具清单\n{tools}\n"
    )
