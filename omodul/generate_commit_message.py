"""Auto-split from hicode whl."""

from __future__ import annotations
from oprim import count_tokens, git_diff
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class GenerateCommitConfig(BaseConfig):
    max_files_to_scan: int = 100
    head_lines_per_file: int = 10
    agents_md_path: str = 'AGENTS.md'
    _omodul_name: ClassVar[str] = 'initialize_project'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'root_path'}
    _enabled_pillars: ClassVar[set[str]] = {'report', 'cost', 'decision_trail'}

class GenerateCommitInput(BaseModel):
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

async def generate_commit_message(
    config: GenerateCommitConfig,
    input_data: GenerateCommitInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """根据 staged diff 生成 commit message。

    支柱：cost（无 trail/report/fingerprint，轻量事务）
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cost = CostTracker()
    status = "completed"
    error = None
    message = ""

    import hashlib
    diff_hash = hashlib.md5((input_data.diff_text or "").encode()).hexdigest()[:8]
    fingerprint = compute_fingerprint({"diff_hash": diff_hash})

    try:
        diff = input_data.diff_text
        if not diff:
            try:
                diff = git_diff(repo=input_data.repo_path, staged=True)
            except Exception:  # pragma: no cover
                diff = git_diff(repo=input_data.repo_path)  # pragma: no cover

        if not diff.strip():
            return build_result(
                status="completed", error=None,
                cost_usd=0.0, message="chore: no changes",
                fingerprint=fingerprint,
            )

        # token 截断
        if count_tokens(diff) > config.max_diff_tokens:
            diff = diff[:config.max_diff_tokens * 4]

        style_hint = {
            "conventional": "Use Conventional Commits format: type(scope): description",
            "imperative":   "Use imperative mood: 'Add feature' not 'Added feature'",
            "descriptive":  "Be descriptive and explain what and why",
        }.get(config.commit_style, "")

        prompt = (
            f"Generate a concise, meaningful git commit message for this diff.\n"
            f"{style_hint}\n"
            f"Return ONLY the commit message, no explanation.\n\n"
            f"```diff\n{diff}\n```"
        )

        response = await input_data.caller(
            messages=[{"role": "user", "content": prompt}],
            tools=None, max_tokens=128,
        )
        cost.add_from_response(response, model=config.llm_model)
        message = extract_text(response).strip().split("\n")[0]   # 取第一行

        if on_step:
            on_step({"event": "completed", "message": message})  # pragma: no cover

    except Exception as exc:
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)}

    return build_result(
        status=status, error=error, cost_usd=cost.total_usd,
        message=message, fingerprint=fingerprint,
    )
