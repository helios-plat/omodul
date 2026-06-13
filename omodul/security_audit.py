"""Auto-split from hicode whl."""

from __future__ import annotations
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class SecurityAuditConfig(BaseConfig):
    focus_areas: list[str] = ['correctness', 'style', 'security', 'performance']
    max_file_tokens: int = 6000
    _omodul_name: ClassVar[str] = 'code_review'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'paths'}
    _enabled_pillars: ClassVar[set[str]] = {'report', 'cost', 'decision_trail'}

class SecurityAuditInput(BaseModel):
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

async def security_audit(
    config: SecurityAuditConfig,
    input_data: SecurityAuditInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """安全扫描：检查常见漏洞、硬编码凭证、注入风险等。

    支柱：report + cost + decision_trail
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    cost = CostTracker()
    status = "completed"
    error = None
    report_path = None

    fingerprint = compute_fingerprint({"paths": sorted(input_data.paths)})

    try:
        trail.record(event="audit_start", paths=input_data.paths)
        if on_step:
            on_step({"event": "audit_start"})

        # 读取所有文件
        code_parts = []
        for path in input_data.paths[:10]:
            content = _read_file_safe(path, config.max_file_tokens)
            code_parts.append(f"## {path}\n```\n{content}\n```")

        code_context = "\n\n".join(code_parts)

        prompt = (
            f"Perform a security audit of this code. Focus on severity >= {config.severity_threshold}.\n"
            f"{'Context: ' + input_data.context if input_data.context else ''}\n\n"
            f"Check for:\n"
            f"- Hardcoded secrets/credentials\n"
            f"- SQL/Command/Path injection\n"
            f"- Authentication/authorization issues\n"
            f"- Insecure dependencies or patterns\n"
            f"- Data validation gaps\n"
            f"- Cryptography misuse\n\n"
            f"For each issue: severity, location, description, recommendation.\n\n"
            f"{code_context}"
        )

        response = await llm_call(
            [{"role": "user", "content": prompt}],
            caller=input_data.caller, cost=cost, trail=trail,
            model=config.llm_model, event="llm_audit",
        )
        audit_text = extract_text(response)
        trail.record(event="audit_done")

        if on_step:
            on_step({"event": "audit_done", "cost_usd": cost.total_usd})

        report_content = f"# Security Audit Report\n\n{audit_text}"
        report_path = write_report(report_content, output_dir=output_dir, name="security_audit")
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
    )
