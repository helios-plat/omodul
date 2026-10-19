"""omodul.self_synthesis_skill_workflow — 技能自主编写与运行时热加载事务.

集成：oprim.dynamic_code_hotload（热加载）+ 注入的 LLM（代码生成）
+ decision_trail 全程留痕。

Pillars: decision_trail, report
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, write_report


class SelfSynthesisSkillConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "self_synthesis_skill_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "report"}

    report_format: str = "markdown"


class SelfSynthesisSkillInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    requirement: str
    module_name: str = "auto_skill"
    llm_caller: Any = None      # prompt 风格 LLM（str -> str）
    hotloader: Any = None       # oprim.dynamic_code_hotload 兼容 callable


async def self_synthesis_skill_workflow(
    config: SelfSynthesisSkillConfig,
    input_data: SelfSynthesisSkillInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """自主编写技能 → 热加载挂载 → 报告。失败绝不 raise。"""
    trail = Trail()

    try:
        if not input_data.requirement.strip():
            raise ValueError("requirement must not be empty")
        if input_data.llm_caller is None or input_data.hotloader is None:
            raise ValueError("llm_caller and hotloader must be injected")

        if on_step:
            on_step({"stage": "code_generation", "status": "running"})

        # Step 1: LLM 自动编写 Python 函数
        code_prompt = (
            "Write a standalone Python function for requirement: "
            f"{input_data.requirement}. Return python code only."
        )
        try:
            python_code = input_data.llm_caller(code_prompt, temperature=0.0)
        except Exception as exc:
            raise ValueError(f"code generation failed: {exc}") from exc
        trail.record(event="code_generated", chars=len(str(python_code)))

        # Step 2: 热加载入当前进程内存
        if on_step:
            on_step({"stage": "hotload", "status": "running"})
        load_res = input_data.hotloader(
            python_code, module_name=input_data.module_name
        )
        trail.record(
            event="hotload_result",
            status=load_res.get("status"),
            module=load_res.get("module_name"),
        )

        if load_res.get("status") == "success":
            exported = load_res.get("exported_functions", [])
            report_path = write_report(
                _render_report(input_data.requirement, input_data.module_name, exported),
                output_dir=Path(output_dir),
                name="self_synthesis_skill",
                fmt=config.report_format,
            )
            return build_result(
                status="completed",
                error=None,
                trail=trail,
                trail_path=trail.write(Path(output_dir)) if output_dir else None,
                report_path=report_path,
                findings={
                    "module_name": load_res.get("module_name"),
                    "exported_functions": exported,
                },
                summary=(
                    f"Successfully synthesized and hotloaded module: "
                    f"{load_res.get('module_name')}"
                ),
            )

        return build_result(
            status="failed",
            error={"type": "hotload_failed", "message": str(load_res.get("error", ""))},
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            summary=f"Hotload failed: {load_res.get('error')}",
        )

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            summary=f"Workflow failed: {exc}",
        )


def _render_report(requirement: str, module_name: str, exported: list) -> str:
    return (
        f"# 技能自主合成报告\n\n"
        f"- 需求: {requirement}\n"
        f"- 模块: `{module_name}`\n"
        f"- 导出函数: {exported}\n"
    )
