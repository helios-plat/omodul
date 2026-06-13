"""Auto-split from hicode whl."""

from __future__ import annotations
from oprim import file_write
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class GenerateTestsConfig(BaseConfig):
    focus_areas: list[str] = ['correctness', 'style', 'security', 'performance']
    max_file_tokens: int = 6000
    _omodul_name: ClassVar[str] = 'code_review'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'paths'}
    _enabled_pillars: ClassVar[set[str]] = {'report', 'cost', 'decision_trail'}

class GenerateTestsInput(BaseModel):
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

async def generate_tests(
    config: GenerateTestsConfig,
    input_data: GenerateTestsInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """为目标源文件生成测试文件。

    支柱：report + cost + decision_trail + fingerprint
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    cost = CostTracker()
    status = "completed"
    error = None
    report_path = None
    test_file_path = None

    fingerprint = compute_fingerprint({"target_path": input_data.target_path})

    try:
        trail.record(event="read_source", path=input_data.target_path)
        source = _read_file_safe(input_data.target_path, config.max_file_tokens)

        # 推导测试文件路径
        src_path = Path(input_data.target_path)
        if input_data.output_test_path:
            test_path = Path(input_data.output_test_path)
        else:
            test_path = src_path.parent / f"test_{src_path.name}"

        if on_step:
            on_step({"event": "generating_tests", "source": input_data.target_path})  # pragma: no cover

        prompt = (
            f"Generate comprehensive {config.test_framework} tests for this code.\n"
            f"Target coverage: {config.coverage_target}%.\n"
            f"Include: happy path, edge cases, error cases.\n"
            f"Return ONLY the test code, no explanation.\n\n"
            f"Source file: {input_data.target_path}\n```python\n{source}\n```"
        )

        response = await llm_call(
            [{"role": "user", "content": prompt}],
            caller=input_data.caller, cost=cost, trail=trail,
            model=config.llm_model, event="generate_tests_llm",
        )
        test_code = extract_text(response)

        # 去除 markdown fence
        import re
        test_code = re.sub(r'^```python\s*\n?', '', test_code, flags=re.MULTILINE)
        test_code = re.sub(r'```\s*$', '', test_code, flags=re.MULTILINE).strip()

        # 写测试文件
        file_write(str(test_path), content=test_code)
        test_file_path = test_path
        trail.record(event="test_file_written", path=str(test_path))

        # report
        report_content = (
            f"# Generated Tests Report\n\n"
            f"**Source**: {input_data.target_path}\n"
            f"**Test file**: {test_path}\n"
            f"**Framework**: {config.test_framework}\n\n"
            f"## Test Code\n```python\n{test_code[:2000]}\n```"
        )
        report_path = write_report(report_content, output_dir=output_dir, name="generate_tests")
        trail.record(event="completed")
        if on_step:
            on_step({"event": "completed", "test_path": str(test_path)})  # pragma: no cover

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
        test_file_path=str(test_file_path) if test_file_path else None,
    )
