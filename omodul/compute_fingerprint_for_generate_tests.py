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

def compute_fingerprint_for_generate_tests(config: GenerateTestsConfig, input_data: GenerateTestsInput) -> str:
    return compute_fingerprint({"target_path": input_data.target_path})  # pragma: no cover
