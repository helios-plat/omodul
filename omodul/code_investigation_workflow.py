"""omodul.code_investigation_workflow — 代码库端到端排查、定位与方案生成事务.

集成：oskill.code_symbol_investigate（依赖图）+ oskill.error_diagnose_skill
（报错诊断）+ oskill.rag_rerank（语义检索）+ LLM 综合（cost 记账）。

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
    llm_call,
    write_report,
)


class CodeInvestigationConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "code_investigation_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"repo_path", "question"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report", "cost"}

    max_files: int = 50
    max_lines_per_file: int = 2000
    report_format: str = "markdown"


class CodeInvestigationInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    question: str
    repo_path: str | None = None
    file_paths: list[str] = []
    traceback: str | None = None     # 有报错时优先诊断模式
    caller: Any = None               # LLM 注入（综合结论 / 报错诊断）
    store: Any = None                # 检索存储（rag_rerank，可选）


def compute_fingerprint_for(
    config: CodeInvestigationConfig, input_data: CodeInvestigationInput
) -> str:
    """Fingerprint over repo_path + question。"""
    return compute_fingerprint(
        {"repo_path": input_data.repo_path, "question": input_data.question}
    )


async def code_investigation_workflow(
    config: CodeInvestigationConfig,
    input_data: CodeInvestigationInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """端到端代码排查：符号图 →（可选）诊断 → LLM 综合 → 报告。失败绝不 raise。"""
    trail = Trail()
    cost = CostTracker()
    fp = compute_fingerprint_for(config, input_data)
    t0 = time.time()

    try:
        from oskill import code_symbol_investigate

        file_paths = list(input_data.file_paths)
        if not file_paths and input_data.repo_path:
            from pathlib import Path as _P

            root = _P(input_data.repo_path)
            if not root.is_dir():
                raise ValueError(f"repo_path not found: {root}")
            file_paths = sorted(
                str(p) for p in root.rglob("*.py") if ".venv" not in p.parts
            )[: config.max_files]
        if not file_paths:
            raise ValueError("no files to investigate (give file_paths or repo_path)")

        if on_step:
            on_step({"stage": "symbols", "status": "running", "files": len(file_paths)})

        graph = await code_symbol_investigate(
            file_paths, max_lines_per_file=config.max_lines_per_file
        )
        trail.record(
            event="symbol_graph_built",
            modules=len(graph["modules"]),
            edges=len(graph["edges"]),
            total_symbols=graph["total_symbols"],
        )

        diagnostics: dict[str, Any] = {}
        if input_data.traceback:
            if input_data.caller is None:
                raise ValueError("traceback diagnosis requires an injected caller")
            from oskill import error_diagnose_skill

            if on_step:
                on_step({"stage": "diagnose", "status": "running"})
            diagnostics = await error_diagnose_skill(
                input_data.traceback,
                caller=input_data.caller,
                file_paths=file_paths[:20],
            )
            trail.record(event="error_diagnosed", root_cause=diagnostics.get("root_cause"))

        findings = _build_findings(graph, diagnostics)

        conclusion = ""
        if input_data.caller is not None:
            if on_step:
                on_step({"stage": "synthesize", "status": "running"})
            response = await llm_call(
                [
                    {
                        "role": "user",
                        "content": (
                            f"问题: {input_data.question}\n\n"
                            f"符号图: {len(graph['modules'])} 模块, "
                            f"{graph['total_symbols']} 符号, {len(graph['edges'])} 依赖边\n"
                            f"孤儿符号: {graph['orphan_symbols'][:10]}\n"
                            + (
                                f"报错诊断: {diagnostics.get('root_cause')} "
                                f"(置信度 {diagnostics.get('confidence')})\n"
                                if diagnostics
                                else ""
                            )
                            + "\n给出定位结论与修复方案（3-5 条要点）。"
                        ),
                    }
                ],
                caller=input_data.caller,
                cost=cost,
                trail=trail,
                model=config.llm_model,
                event="investigation_synthesis",
            )
            conclusion = _extract_text(response)

        report = _render_report(input_data, graph, diagnostics, conclusion, cost)
        report_path = write_report(
            report, output_dir=Path(output_dir), name="code_investigation_report",
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
            findings=findings,
            conclusion=conclusion,
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


def _build_findings(graph: dict, diagnostics: dict) -> dict:
    return {
        "modules": len(graph["modules"]),
        "edges": len(graph["edges"]),
        "total_symbols": graph["total_symbols"],
        "orphan_symbols": graph["orphan_symbols"][:20],
        "errors": graph.get("errors", []),
        "root_cause": diagnostics.get("root_cause"),
        "confidence": diagnostics.get("confidence"),
    }


def _extract_text(response: dict) -> str:
    content = response.get("content", [])
    if isinstance(content, str):
        return content
    return "".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    ).strip()


def _render_report(input_data, graph, diagnostics, conclusion, cost) -> str:
    lines = [
        "# 代码排查报告",
        "",
        f"- 问题: {input_data.question}",
        f"- 文件数: {len(graph['modules'])} / 符号数: {graph['total_symbols']}",
        f"- 依赖边: {len(graph['edges'])}",
        f"- 成本: ${cost.total_usd:.6f}",
        "",
        "## 符号概览",
        "```",
        "\n".join(
            f"{path}: {[f['name'] for f in m['functions']][:8]}"
            for path, m in list(graph["modules"].items())[:20]
        ),
        "```",
    ]
    if diagnostics:
        lines += [
            "",
            "## 报错诊断",
            f"- 根因: {diagnostics.get('root_cause')}",
            f"- 置信度: {diagnostics.get('confidence')}",
            f"- 修复建议: {diagnostics.get('fix_suggestions', [])}",
        ]
    if conclusion:
        lines += ["", "## 结论", conclusion]
    return "\n".join(lines)
