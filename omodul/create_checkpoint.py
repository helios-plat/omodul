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

class CreateCheckpointConfig(BaseConfig):
    max_files: int = 20
    max_file_tokens: int = 5000
    dry_run: bool = False
    _omodul_name: ClassVar[str] = 'refactor_transaction'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'instruction', 'paths'}
    _enabled_pillars: ClassVar[set[str]] = {'decision_trail', 'fingerprint', 'cost', 'report'}

class CreateCheckpointInput(BaseModel):
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

async def create_checkpoint(
    config: CreateCheckpointConfig,
    input_data: CreateCheckpointInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """快照当前会话 + 代码状态（rewind 之根）。

    支柱：fingerprint + decision_trail
    """
    import uuid
    import time
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trail = Trail()
    status = "completed"
    error = None
    checkpoint_id = f"ckpt_{uuid.uuid4().hex[:8]}"
    revision = ""

    fingerprint = compute_fingerprint({
        "session_id": input_data.session_id,
        "message_count": len(input_data.messages),
    })

    try:
        trail.record(event="checkpoint_start", checkpoint_id=checkpoint_id)

        payload = json.dumps({
            "checkpoint_id": checkpoint_id,
            "session_id": input_data.session_id,
            "messages": input_data.messages,
            "message_count": len(input_data.messages),
            "created_at": time.time(),
            "metadata": input_data.metadata,
        }, ensure_ascii=False)

        if input_data.store is not None:
            key = f"session:{input_data.session_id}:checkpoint:{checkpoint_id}"
            revision = await input_data.store.save(key=key, value=payload)
        else:
            # 本地文件 fallback
            ckpt_path = output_dir / f"{checkpoint_id}.json"
            ckpt_path.write_text(payload)
            revision = str(ckpt_path)

        trail.record(event="checkpoint_saved", revision=revision)
        if on_step:
            on_step({"event": "checkpoint_saved", "checkpoint_id": checkpoint_id})

    except Exception as exc:
        status = "failed"
        error = {"type": type(exc).__name__, "message": str(exc)}
        trail.record(event="error", error=error)
    finally:
        trail_path = trail.write(output_dir)

    return build_result(
        status=status, error=error, fingerprint=fingerprint,
        trail=trail, trail_path=trail_path,
        cost_usd=0.0,
        checkpoint_id=checkpoint_id, revision=revision,
        message_count=len(input_data.messages),
    )
