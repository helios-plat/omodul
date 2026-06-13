"""Auto-split from hicode whl."""

from __future__ import annotations
import json
import re
import sys
import os
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel
from ._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint, extract_text, llm_call, write_report

class RewindConfig(BaseConfig):
    max_files: int = 20
    max_file_tokens: int = 5000
    dry_run: bool = False
    _omodul_name: ClassVar[str] = 'refactor_transaction'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'instruction', 'paths'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'fingerprint', 'cost', 'report'}

class RewindInput(BaseModel):
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

async def rewind_to_checkpoint(
    config: RewindConfig,
    input_data: RewindInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """从 checkpoint 恢复会话状态。

    支柱：decision_trail
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    status = "completed"
    error = None
    messages = []
    metadata = {}

    try:
        trail.record(event="rewind_start", checkpoint_id=input_data.checkpoint_id)

        if input_data.store is not None:
            # 从 store 查找（需要知道 session_id，这里简化：直接用 checkpoint_id 查）
            key = f"checkpoint:{input_data.checkpoint_id}"
            raw = await input_data.store.load(key=key)
            if raw is None:
                raise ValueError(f"checkpoint not found: {input_data.checkpoint_id}")
            data = json.loads(raw)
        elif input_data.checkpoint_path:
            data = json.loads(Path(input_data.checkpoint_path).read_text())
        else:
            raise ValueError("must provide store or checkpoint_path")

        messages = data.get("messages", [])
        metadata = data.get("metadata", {})
        trail.record(event="rewind_done", message_count=len(messages))
        if on_step:
            on_step({"event": "rewind_done", "message_count": len(messages)})

    except Exception as exc:
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)}
        trail.record(event="error", error=error)
    finally:
        trail_path = trail.write(output_dir)

    return build_result(
        status=status, error=error,
        trail=trail, trail_path=trail_path,
        cost_usd=0.0,
        messages=messages, metadata=metadata,
        message_count=len(messages),
    )
