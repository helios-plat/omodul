"""Auto-split from hicode whl."""

from __future__ import annotations
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class SummarizeSessionConfig(BaseConfig):
    max_files_to_scan: int = 100
    head_lines_per_file: int = 10
    agents_md_path: str = 'AGENTS.md'
    _omodul_name: ClassVar[str] = 'initialize_project'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'root_path'}
    _enabled_pillars: ClassVar[set[str]] = {'report', 'cost', 'decision_trail'}

class SummarizeSessionInput(BaseModel):
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

async def summarize_session(
    config: SummarizeSessionConfig,
    input_data: SummarizeSessionInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """将对话历史压缩为长期记忆摘要。

    支柱：cost
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cost = CostTracker()
    status = "completed"
    error = None
    summary = ""

    try:
        msgs = input_data.messages[:config.max_messages]
        if not msgs:
            return build_result(status="completed", error=None, cost_usd=0.0,
                                summary="(empty session)")

        history = "\n".join(
            f"[{m.get('role','?')}]: {_msg_text(m)[:300]}" for m in msgs
        )
        detail = "in detail, preserving key decisions and code changes" \
            if config.summary_length == "detailed" else "briefly in 3-5 sentences"

        response = await input_data.caller(
            messages=[{"role": "user", "content":
                f"Summarize this AI coding session {detail}, focusing on what was accomplished:\n\n{history[:8000]}"}],
            tools=None, max_tokens=512,
        )
        cost.add_from_response(response, model=config.llm_model)
        summary = extract_text(response).strip()
        if on_step:
            on_step({"event": "completed", "summary_len": len(summary)})

    except Exception as exc:
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)}

    return build_result(status=status, error=error, cost_usd=cost.total_usd, summary=summary)
