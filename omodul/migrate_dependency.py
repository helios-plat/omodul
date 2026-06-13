"""Auto-split from hicode whl."""

from __future__ import annotations
from oprim import file_read, glob_match
import json
import re
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class MigrateDependencyConfig(BaseConfig):
    max_files: int = 20
    max_file_tokens: int = 5000
    dry_run: bool = False
    _omodul_name: ClassVar[str] = 'refactor_transaction'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'instruction', 'paths'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'fingerprint', 'cost', 'report'}

class MigrateDependencyInput(BaseModel):
    instruction: str
    paths: list[str]
    caller: Any
    context: str = ''

class RunAndFixConfig(BaseConfig):
    max_iterations: int = 5
    timeout: int = 60
    _omodul_name: ClassVar[str] = 'run_and_fix'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'command'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'cost', 'report'}

class RunAndFixInput(BaseModel):
    command: str
    cwd: str
    caller: Any
    target_files: list[str] = []

class MigrateDependencyConfig(BaseConfig):
    max_files: int = 50
    max_file_tokens: int = 3000
    _omodul_name: ClassVar[str] = 'migrate_dependency'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'dependency', 'target_version'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'fingerprint', 'cost', 'report'}

class MigrateDependencyInput(BaseModel):
    dependency: str
    target_version: str
    root_path: str
    caller: Any

class CreateCheckpointConfig(BaseConfig):
    _omodul_name: ClassVar[str] = 'create_checkpoint'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'session_id', 'message_count'}
    _enabled_pillars: ClassVar[set[str]] = {'fingerprint', 'decision_trail'}

class CreateCheckpointInput(BaseModel):
    messages: list[dict]
    session_id: str = ''
    metadata: dict = {}
    store: Any = None

class RewindConfig(BaseConfig):
    _omodul_name: ClassVar[str] = 'rewind_to_checkpoint'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = set()
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail'}

class RewindInput(BaseModel):
    checkpoint_id: str
    store: Any = None
    checkpoint_path: str = ''

class CompactConversationConfig(BaseConfig):
    target_budget: int = 4000
    _omodul_name: ClassVar[str] = 'compact_conversation'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'message_count', 'last_msg_hash'}
    _enabled_pillars: ClassVar[set[str]] = {'cost', 'decision_trail', 'fingerprint'}

class CompactConversationInput(BaseModel):
    messages: list[dict]
    caller: Any
    session_id: str = ''

class InstallPluginConfig(BaseConfig):
    _omodul_name: ClassVar[str] = 'install_plugin'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'plugin_name', 'version'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'fingerprint'}

class InstallPluginInput(BaseModel):
    plugin_bundle: dict
    install_dir: str

async def migrate_dependency(
    config: MigrateDependencyConfig,
    input_data: MigrateDependencyInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """升级指定依赖到目标版本，LLM 生成迁移指令。

    支柱：decision_trail + fingerprint + cost + report
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    cost = CostTracker()
    status = "completed"
    error = None
    report_path = None

    fingerprint = compute_fingerprint({
        "dependency": input_data.dependency,
        "target_version": input_data.target_version,
    })

    try:
        trail.record(event="migrate_start",
                     dep=input_data.dependency, version=input_data.target_version)
        if on_step:
            on_step({"event": "migrate_start"})

        from oprim.fs import glob_match
        # 查找使用此依赖的文件
        try:
            all_files = glob_match("**/*.py", root=input_data.root_path)[:config.max_files]
        except Exception:  # pragma: no cover
            all_files = []  # pragma: no cover

        dep_files = []
        for p in all_files:
            try:
                content = file_read(str(p))
                if input_data.dependency.lower() in content.lower():
                    dep_files.append((str(p), content))
            except Exception:  # pragma: no cover
                pass  # pragma: no cover

        trail.record(event="files_found", count=len(dep_files))

        file_ctx = "\n\n".join(
            f"## {p}\n```python\n{c[:config.max_file_tokens * 4]}\n```"
            for p, c in dep_files[:8]
        )

        prompt = (
            f"Generate migration instructions for upgrading `{input_data.dependency}` "
            f"to version `{input_data.target_version}`.\n\n"
            f"Provide:\n"
            f"1. Breaking changes to be aware of\n"
            f"2. Required code changes (as JSON edits if applicable)\n"
            f"3. Configuration changes\n"
            f"4. Testing recommendations\n\n"
            f"Affected files:\n{file_ctx}"
        )

        response = await llm_call(
            [{"role": "user", "content": prompt}],
            caller=input_data.caller, cost=cost, trail=trail,
            model=config.llm_model, event="generate_migration_plan",
        )
        migration_text = extract_text(response)
        trail.record(event="plan_generated")

        report_content = (
            f"# Migration Report: {input_data.dependency} → {input_data.target_version}\n\n"
            f"**Affected files**: {len(dep_files)}\n\n"
            f"{migration_text}"
        )
        report_path = write_report(report_content, output_dir=output_dir, name="migrate_dependency")
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
    )
