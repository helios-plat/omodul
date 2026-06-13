"""Auto-split from hicode whl. Verify before use."""

from __future__ import annotations
import sys as _sys
import os as _os
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from pydantic import BaseModel

@dataclass
class VersionStore:
    """
    obase.versionstore 占位。
    生产版持久化到 .hicode/snapshots/；此处用内存 + 临时目录模拟。
    """
    _snapshots: dict[str, dict[str, str]] = field(default_factory=dict)

    def snapshot(self, paths: list[Path]) -> str:
        """打快照：{rev_id -> {path -> content}}。路径不存在记 None。"""
        rev = str(uuid.uuid4())[:8]
        self._snapshots[rev] = {str(p): p.read_text(encoding='utf-8', errors='replace') if p.exists() else None for p in paths}
        return rev

    def restore(self, rev: str) -> list[str]:
        """还原快照中所有文件，返回已还原路径列表。"""
        snap = self._snapshots.get(rev, {})
        restored = []
        for path_str, content in snap.items():
            p = Path(path_str)
            if content is None:
                if p.exists():
                    p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding='utf-8')
            restored.append(path_str)
        return restored

    def list_revs(self) -> list[str]:
        return list(self._snapshots.keys())

class EditBlock(BaseModel):
    """apply_edit_block 格式：<<<SEARCH / >>>REPLACE 块。"""
    search: str
    replace: str

class Edit(BaseModel):
    """单文件编辑指令。三种格式之一。"""
    path: str
    full_content: str | None = None
    blocks: list[EditBlock] | None = None
    unified_diff: str | None = None
    create_if_missing: bool = True
    validate_syntax: bool = True

class ChangesetConfig(BaseModel):
    """omodul BaseConfig（§5.3 标准）。"""
    sandbox_root: str = ''
    syntax_check_enabled: bool = True
    overwrite: bool = True
    _omodul_name: ClassVar[str] = 'apply_changeset'
    _omodul_version: ClassVar[str] = '1.0.0'
    _fingerprint_fields: ClassVar[set[str]] = {'path', 'full_content', 'blocks', 'unified_diff'}
    _enabled_pillars: ClassVar[set[str]] = {'fingerprint', 'decision_trail'}

class ChangesetInput(BaseModel):
    edits: list[Edit]
    versionstore: Any = None
    message: str = ''

def compute_fingerprint_for(
    config: ChangesetConfig,
    input_data: ChangesetInput,
) -> str:
    """
    暴露给服务层用于去重判断（§5.5 MUST，启用 fingerprint 时）。
    fingerprint = sha256(canonical edit list)，与文件现有内容无关。
    """
    key_parts = []
    for edit in sorted(input_data.edits, key=lambda e: e.path):
        key_parts.append({
            "path": edit.path,
            "full_content": edit.full_content,
            "blocks": [(b.search, b.replace) for b in (edit.blocks or [])],
            "unified_diff": edit.unified_diff,
        })
    canonical = json.dumps(key_parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]
