"""Auto-split from hicode whl."""

from __future__ import annotations
from oprim import file_read, file_write, glob_match
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

async def initialize_project(
    config: InitProjectConfig,
    input_data: InitProjectInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """/init：扫描代码库 → LLM 分析 → 写 AGENTS.md。

    支柱：report + cost + decision_trail
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    cost = CostTracker()
    status = "completed"
    error = None
    report_path = None

    fingerprint = compute_fingerprint({"root_path": input_data.root_path})

    try:
        root = Path(input_data.root_path)
        trail.record(event="scan_start", root=str(root))
        if on_step:
            on_step({"event": "scan_start"})

        # 扫描文件结构
        try:
            py_files = glob_match("**/*.py", root=str(root))[:config.max_files_to_scan]
            ts_files = glob_match("**/*.ts", root=str(root))[:20]
            all_files = py_files + ts_files
        except Exception:  # pragma: no cover
            all_files = []  # pragma: no cover

        file_summaries = []
        for p in all_files[:30]:
            try:  # pragma: no cover
                content = file_read(str(p))  # pragma: no cover
                head = "\n".join(content.splitlines()[:config.head_lines_per_file])  # pragma: no cover
                rel = str(Path(p).relative_to(root)) if hasattr(p, 'relative_to') else str(p)  # pragma: no cover
                file_summaries.append(f"### {rel}\n```\n{head}\n```")  # pragma: no cover
            except Exception:  # pragma: no cover
                pass  # pragma: no cover

        summary_text = "\n\n".join(file_summaries[:20])
        trail.record(event="scan_done", files_found=len(all_files))

        # LLM 分析
        prompt = (
            f"Analyze this codebase and generate a concise AGENTS.md file that describes:\n"
            f"1. Project overview and purpose\n"
            f"2. Key directories and their roles\n"
            f"3. Important conventions and patterns\n"
            f"4. How to run tests and build\n"
            f"5. Key files an AI agent should know about\n\n"
            f"File structure sample:\n{summary_text[:6000]}\n\n"
            f"Generate the AGENTS.md content in Markdown format."
        )

        response = await llm_call(
            [{"role": "user", "content": prompt}],
            caller=input_data.caller,
            cost=cost, trail=trail, model=config.llm_model,
            event="generate_agents_md",
        )
        agents_md_content = extract_text(response)

        if on_step:
            on_step({"event": "llm_done", "cost_usd": cost.total_usd})

        # 写 AGENTS.md
        agents_path = root / config.agents_md_path
        file_write(str(agents_path), content=agents_md_content)
        trail.record(event="agents_md_written", path=str(agents_path))

        # report
        report_content = f"# initialize_project Report\n\n{agents_md_content}"
        report_path = write_report(report_content, output_dir=output_dir,
                                   name="initialize_project")
        trail.record(event="completed")
        if on_step:
            on_step({"event": "completed"})

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
        agents_md_path=str(root / config.agents_md_path) if status == "completed" else None,
    )
