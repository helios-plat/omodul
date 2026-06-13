"""Auto-split from hicode whl."""

from __future__ import annotations
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class CodeReviewConfig(BaseConfig):
    focus_areas: list[str] = ['correctness', 'style', 'security', 'performance']
    max_file_tokens: int = 6000
    _omodul_name: ClassVar[str] = 'code_review'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'paths'}
    _enabled_pillars: ClassVar[set[str]] = {'report', 'cost', 'decision_trail'}

class CodeReviewInput(BaseModel):
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

async def code_review(
    config: CodeReviewConfig,
    input_data: CodeReviewInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """对文件集或 diff 做 LLM 代码评审，输出 Markdown 报告。

    支柱：report + cost + decision_trail
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    cost = CostTracker()
    status = "completed"
    error = None
    report_path = None

    fingerprint = compute_fingerprint({
        "paths": sorted(input_data.paths),
        "diff_hash": hash(input_data.diff_text) if input_data.diff_text else 0,
    })

    try:
        trail.record(event="review_start", paths=input_data.paths)
        if on_step:
            on_step({"event": "review_start", "n_files": len(input_data.paths)})

        # 构建代码上下文
        code_parts = []
        if input_data.diff_text:
            code_parts.append(f"## Diff\n```diff\n{input_data.diff_text[:8000]}\n```")
        for path in input_data.paths[:10]:
            content = _read_file_safe(path, config.max_file_tokens)
            code_parts.append(f"## {path}\n```\n{content}\n```")

        code_context = "\n\n".join(code_parts)
        focus = ", ".join(config.focus_areas)

        prompt = (
            f"Review this code focusing on: {focus}.\n"
            f"{'Context: ' + input_data.context if input_data.context else ''}\n\n"
            f"Provide:\n"
            f"1. Executive summary\n"
            f"2. Issues found (with severity: critical/major/minor)\n"
            f"3. Positive aspects\n"
            f"4. Specific recommendations\n\n"
            f"{code_context}"
        )

        response = await llm_call(
            [{"role": "user", "content": prompt}],
            caller=input_data.caller, cost=cost, trail=trail,
            model=config.llm_model, event="llm_review",
        )
        review_text = extract_text(response)
        trail.record(event="review_done", tokens=cost.out_tokens)

        if on_step:
            on_step({"event": "review_done", "cost_usd": cost.total_usd})

        report_content = f"# Code Review Report\n\n{review_text}"
        report_path = write_report(report_content, output_dir=output_dir, name="code_review")
        trail.record(event="completed")

    except Exception as exc:
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)}
        trail.record(event="error", error=error)
    finally:
        trail_path = trail.write(output_dir)

    return build_result(
        status=status, error=error, fingerprint=fingerprint,
        trail=trail, trail_path=trail_path, report_path=report_path,
        cost_usd=cost.total_usd,
        findings={"review_length": len(extract_text(response)) if status == "completed" else 0},
    )
