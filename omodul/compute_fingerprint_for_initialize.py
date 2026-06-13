"""Auto-split from hicode whl."""

from __future__ import annotations
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class InitProjectConfig(BaseConfig):
    max_files_to_scan: int = 100
    head_lines_per_file: int = 10
    agents_md_path: str = 'AGENTS.md'
    _omodul_name: ClassVar[str] = 'initialize_project'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'root_path'}
    _enabled_pillars: ClassVar[set[str]] = {'report', 'cost', 'decision_trail'}

class InitProjectInput(BaseModel):
    root_path: str
    caller: Any

class GenerateCommitConfig(BaseConfig):
    max_diff_tokens: int = 3000
    commit_style: str = 'conventional'
    _omodul_name: ClassVar[str] = 'generate_commit_message'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'diff_hash'}
    _enabled_pillars: ClassVar[set[str]] = {'cost'}

class GenerateCommitInput(BaseModel):
    repo_path: str
    caller: Any
    diff_text: str = ''

class SummarizeSessionConfig(BaseConfig):
    max_messages: int = 200
    summary_length: str = 'brief'
    _omodul_name: ClassVar[str] = 'summarize_session'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = set()
    _enabled_pillars: ClassVar[set[str]] = {'cost'}

class SummarizeSessionInput(BaseModel):
    messages: list[dict]
    caller: Any
    session_id: str = ''

def compute_fingerprint_for_initialize(config: InitProjectConfig, input_data: InitProjectInput) -> str:
    return compute_fingerprint({"root_path": input_data.root_path})  # pragma: no cover
