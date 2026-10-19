"""omodul.root_cause_analysis_workflow — 失败根因因果分析与反脆弱修补事务.

集成：oprim.causal_graph_build（因果 DAG）+ oskill.counterfactual_reasoning
（反事实推演，LLM 注入）+ decision_trail 全程留痕。

Pillars: decision_trail, report
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, write_report


class RootCauseAnalysisConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "root_cause_analysis_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "report"}

    strict_mode: bool = True
    report_format: str = "markdown"


class RootCauseAnalysisInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    decision_trail: list[dict] = []
    failed_step: str = ""
    alternative_action: str = "Auto-fix action"
    causal_builder: Any = None     # oprim.causal_graph_build 兼容 callable
    counterfactual_skill: Any = None  # oskill.counterfactual_reasoning 兼容 callable
    llm_caller: Any = None         # LLM（透传反事实推演）


async def root_cause_analysis_workflow(
    config: RootCauseAnalysisConfig,
    input_data: RootCauseAnalysisInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """分析失败根因：因果图 → 反事实推演 → 报告。失败绝不 raise。"""
    trail = Trail()
    t0 = time.time()

    try:
        if not input_data.decision_trail:
            raise ValueError("no decision trail provided for root cause analysis")
        if input_data.causal_builder is None or input_data.counterfactual_skill is None:
            raise ValueError("causal_builder and counterfactual_skill must be injected")

        if on_step:
            on_step({"stage": "causal_graph", "status": "running"})

        # 1. 原子操作：生成因果图
        graph_res = input_data.causal_builder(
            input_data.decision_trail, strict_mode=config.strict_mode
        )
        trail.record(
            event="causal_graph_built",
            nodes=graph_res.get("node_count", 0),
            edges=graph_res.get("edge_count", 0),
        )

        # 2. 算法技能：反事实推理
        if on_step:
            on_step({"stage": "counterfactual", "status": "running"})
        cf_res = input_data.counterfactual_skill(
            causal_graph=graph_res.get("graph", {}),
            failed_step_id=input_data.failed_step,
            llm_caller=input_data.llm_caller,
            alternative_action=input_data.alternative_action,
        )
        trail.record(
            event="counterfactual_done",
            would_succeed=cf_res.get("would_succeed"),
            status=cf_res.get("status"),
        )

        report = {
            "root_cause": cf_res.get("root_cause"),
            "would_succeed": cf_res.get("would_succeed"),
            "explanation": cf_res.get("explanation"),
            "graph_stats": {
                "nodes": graph_res.get("node_count"),
                "edges": graph_res.get("edge_count"),
            },
            "elapsed_s": round(time.time() - t0, 2),
        }

        report_path = write_report(
            _render_report(report),
            output_dir=Path(output_dir),
            name="root_cause_analysis",
            fmt=config.report_format,
        )

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            report_path=report_path,
            findings=report,
            summary=f"Root cause analysis finished. Primary cause: {report['root_cause']}",
        )

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            summary=f"Workflow failed with error: {exc}",
        )


def _render_report(report: dict) -> str:
    return (
        f"# 根因分析报告\n\n"
        f"- 根因: {report.get('root_cause')}\n"
        f"- 反事实会成功: {report.get('would_succeed')}\n"
        f"- 图规模: {report.get('graph_stats', {})}\n"
        f"- 耗时: {report.get('elapsed_s')}s\n\n"
        f"## 解释\n{report.get('explanation', '')}\n"
    )
