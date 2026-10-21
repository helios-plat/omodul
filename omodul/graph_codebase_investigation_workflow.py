"""omodul.graph_codebase_investigation_workflow — 基于 AST 图谱的代码库结构快速理解与影响面推演事务.

Pillars: fingerprint, decision_trail, report

签名: (config, input_data, output_dir, *, on_step=None)
失败绝对不 raise — 返回 status="failed".

遵循 omodul 红线:
- 显式声明 _enabled_pillars
- 含 "failed" 状态返回
- 禁止 try 之外裸 raise
- 禁止 sibling omodul 导入
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class Config(BaseConfig):
    """Graph Codebase Investigation Workflow 配置."""

    _omodul_name: ClassVar[str] = "graph_codebase_investigation_workflow"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}

    parse_op: Any = None
    impact_sk: Any = None
    llm_caller: Any = None


class InputData(BaseModel):
    """Graph Codebase Investigation Workflow 输入."""

    file_content: str = "class Service: pass"
    file_path: str = "sample.py"
    target_node: str = "sample.py::Service"

    model_config = {"arbitrary_types_allowed": True}


def graph_codebase_investigation_workflow(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """基于 AST 图谱的代码库结构快速理解与影响面推演业务事务.

    流程:
    1. 解析代码 AST 生成图（节点 + 边）
    2. 对目标节点执行影响面分析
    3. 生成可视化报告

    Args:
        config: 配置（含注入的 parse_op / impact_sk / llm_caller）
        input_data: 输入数据
        output_dir: 输出目录
        on_step: 步骤回调（可选）

    Returns:
        {"status": "completed"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []

    parse_op = config.parse_op
    impact_sk = config.impact_sk
    llm_caller = config.llm_caller

    code_content = input_data.file_content
    file_path = input_data.file_path
    target_node = input_data.target_node

    try:
        # Phase 1: AST 解析生成图
        trail.record(event="graph_parse", file_path=file_path)
        if parse_op is not None:
            graph_res = parse_op(code_content, file_path=file_path)
        else:
            graph_res = {"status": "success", "nodes": [], "edges": []}

        nodes_count = len(graph_res.get("nodes", []))
        edges_count = len(graph_res.get("edges", []))
        decision_trail.append({
            "step": "graph_parsed",
            "nodes_count": nodes_count,
            "edges_count": edges_count,
        })

        # Phase 2: 影响面分析
        trail.record(event="impact_analysis", target_node=target_node)
        if impact_sk is not None and llm_caller is not None:
            impact_res = impact_sk(
                graph_res,
                target_node_id=target_node,
                llm_caller=llm_caller,
            )
        else:
            impact_res = {
                "impacted_nodes": [],
                "downstream_deps": [],
                "risk_level": "low",
                "plan": "No analysis available.",
            }

        decision_trail.append({
            "step": "impact_analyzed",
            "risk": impact_res.get("risk_level"),
            "impacted_count": len(impact_res.get("impacted_nodes", [])),
        })

        cost.add(in_tok=600, out_tok=350)

        # 生成报告
        nodes_summary = "\n".join(
            f"- [{n.get('type', '?')}] {n.get('id', n.get('name', '?'))}"
            for n in graph_res.get("nodes", [])[:30]
        )
        report_content = (
            f"# Graph Codebase Investigation Report\n\n"
            f"## File: {file_path}\n"
            f"## Target Node: {target_node}\n\n"
            f"### Graph Summary\n"
            f"- Nodes: {nodes_count}\n"
            f"- Edges: {edges_count}\n\n"
            f"### Nodes:\n{nodes_summary}\n\n"
            f"### Impact Analysis\n"
            f"- Risk Level: **{impact_res.get('risk_level', 'N/A')}**\n"
            f"- Upstream Impacted: {impact_res.get('impacted_nodes', [])}\n"
            f"- Downstream Dependencies: {impact_res.get('downstream_deps', [])}\n"
            f"- Mitigation Plan: {impact_res.get('plan', 'N/A')}\n"
        )
        report_path = write_report(
            report_content, output_dir=output_dir,
            name=f"graph_analysis_{Path(file_path).stem}",
            fmt="markdown",
        )

        trail.write(output_dir)

        return build_result(
            status="completed",
            error=None,
            cost_usd=cost.total_usd,
            report_path=str(report_path),
            trail=trail,
            trail_path=trail.write(output_dir),
            summary=f"Graph Codebase Investigation finished for node: {target_node}",
            pillars={
                "fingerprint": {"target_node": target_node, "file": file_path},
                "decision_trail": decision_trail,
                "report": {
                    "impact_analysis": impact_res,
                    "graph_nodes": graph_res.get("nodes"),
                    "graph_edges": graph_res.get("edges"),
                },
            },
        )

    except Exception as e:
        return build_result(
            status="failed",
            error={"type": type(e).__name__, "message": str(e)},
            cost_usd=cost.total_usd,
            trail=trail,
            summary=f"Graph Codebase Investigation failed: {str(e)}",
            pillars={"decision_trail": decision_trail, "report": {"error": str(e)}},
        )
