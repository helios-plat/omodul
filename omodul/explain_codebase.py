"""Auto-split from hicode whl."""

from __future__ import annotations
from oprim import glob_match
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class ExplainCodebaseConfig(BaseConfig):
    focus_areas: list[str] = ['correctness', 'style', 'security', 'performance']
    max_file_tokens: int = 6000
    _omodul_name: ClassVar[str] = 'code_review'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'paths'}
    _enabled_pillars: ClassVar[set[str]] = {'report', 'cost', 'decision_trail'}

class ExplainCodebaseInput(BaseModel):
    paths: list[str]
    caller: Any
    diff_text: str = ''
    context: str = ''

class GenerateTestsConfig(BaseConfig):
    test_framework: str = 'pytest'
    coverage_target: int = 80
    max_file_tokens: int = 6000
    _omodul_name: ClassVar[str] = 'generate_tests'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'target_path'}
    _enabled_pillars: ClassVar[set[str]] = {'report', 'cost', 'decision_trail', 'fingerprint'}

class GenerateTestsInput(BaseModel):
    target_path: str
    caller: Any
    output_test_path: str = ''

class ExplainCodebaseConfig(BaseConfig):
    scope: str = 'full'
    max_files: int = 20
    max_file_tokens: int = 2000
    _omodul_name: ClassVar[str] = 'explain_codebase'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'root_path'}
    _enabled_pillars: ClassVar[set[str]] = {'report', 'cost'}

class ExplainCodebaseInput(BaseModel):
    root_path: str
    caller: Any
    focus: str = ''

class SecurityAuditConfig(BaseConfig):
    severity_threshold: str = 'medium'
    max_file_tokens: int = 5000
    _omodul_name: ClassVar[str] = 'security_audit'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'paths'}
    _enabled_pillars: ClassVar[set[str]] = {'report', 'cost', 'decision_trail'}

class SecurityAuditInput(BaseModel):
    paths: list[str]
    caller: Any
    context: str = ''

async def explain_codebase(
    config: ExplainCodebaseConfig,
    input_data: ExplainCodebaseInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """生成代码库解释报告（架构 / 关键模块 / 数据流）。

    支柱：report + cost
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cost = CostTracker()
    status = "completed"
    error = None
    report_path = None

    fingerprint = compute_fingerprint({"root_path": input_data.root_path,
                                        "focus": input_data.focus})

    try:
        root = Path(input_data.root_path)
        if on_step:
            on_step({"event": "scanning"})

        # 收集文件摘要
        try:
            files = glob_match("**/*.py", root=str(root))[:config.max_files]
        except Exception:  # pragma: no cover
            files = []  # pragma: no cover

        file_parts = []
        for p in files:
            content = _read_file_safe(str(p), config.max_file_tokens)
            rel = str(Path(str(p)).relative_to(root))
            file_parts.append(f"### {rel}\n```\n{content[:800]}\n```")

        files_context = "\n\n".join(file_parts[:15])
        focus_line = f"Focus especially on: {input_data.focus}\n" if input_data.focus else ""

        prompt = (
            f"Explain this codebase clearly for a developer new to the project.\n"
            f"{focus_line}"
            f"Include:\n"
            f"1. High-level architecture and design\n"
            f"2. Key modules and their responsibilities\n"
            f"3. Important patterns and conventions\n"
            f"4. Data flow and key interactions\n"
            f"5. Entry points\n\n"
            f"{files_context}"
        )

        response = await input_data.caller(
            messages=[{"role": "user", "content": prompt}],
            tools=None, max_tokens=2048,
        )
        cost.add_from_response(response, model=config.llm_model)
        explanation = extract_text(response)

        report_path = write_report(
            f"# Codebase Explanation\n\n{explanation}",
            output_dir=output_dir, name="explain_codebase",
        )
        if on_step:
            on_step({"event": "completed"})

    except Exception as exc:
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)}

    return build_result(
        status=status, error=error, fingerprint=fingerprint,
        report_path=report_path, cost_usd=cost.total_usd,
    )
