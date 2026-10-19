"""3O 范式全栈 — omodul 新增事务测试 (元素 22/23/24)."""

from __future__ import annotations

import asyncio
import json

import pytest
from obase import LocalVFS, SQLiteKVStore
from pydantic import BaseModel

from omodul import (
    EvidenceConfig,
    EvidenceInput,
    SessionNodeInput,
    SessionTreeConfig,
    ToolConfig,
    ToolInput,
    evidence_refine_workflow,
    run_tool_pipeline,
    session_tree_upsert_workflow,
)


class ShellCmd(BaseModel):
    command: str
    timeout: int = 30


# ---------------------------------------------------------------------------
# 元素 22: tool_pipeline
# ---------------------------------------------------------------------------


async def test_tool_pipeline_success(tmp_path):
    vfs = LocalVFS(str(tmp_path))
    result = await run_tool_pipeline(
        ToolConfig(),
        ToolInput(
            raw_llm_text='{"tool_name": "shell_exec", "args": {"command": "echo hi"}}',
            allowed_schemas={"shell_exec": ShellCmd},
        ),
        tmp_path,
        vfs=vfs,
    )
    assert result["status"] == "completed"
    assert result["error"] is None
    assert result["findings"]["exit_code"] == 0
    assert "hi" in result["findings"]["stdout"]
    trail = json.loads((tmp_path / "decision_trail.json").read_text())
    assert trail[0]["action"] == "validate"


async def test_tool_pipeline_not_permitted(tmp_path):
    vfs = LocalVFS(str(tmp_path))
    result = await run_tool_pipeline(
        ToolConfig(),
        ToolInput(
            raw_llm_text='{"tool_name": "rm_rf", "args": {"path": "/"}}',
            allowed_schemas={"shell_exec": ShellCmd},
        ),
        tmp_path,
        vfs=vfs,
    )
    assert result["status"] == "failed"
    assert "not permitted" in result["error"]["message"]


async def test_tool_pipeline_invalid_json(tmp_path):
    vfs = LocalVFS(str(tmp_path))
    result = await run_tool_pipeline(
        ToolConfig(),
        ToolInput(raw_llm_text="{broken", allowed_schemas={"shell_exec": ShellCmd}),
        tmp_path,
        vfs=vfs,
    )
    assert result["status"] == "failed"
    assert "Invalid Tool JSON" in result["error"]["message"]


async def test_tool_pipeline_cancelled_reraised(tmp_path):
    vfs = LocalVFS(str(tmp_path))

    def cancel(_: dict) -> None:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_tool_pipeline(
            ToolConfig(),
            ToolInput(
                raw_llm_text='{"tool_name": "shell_exec", "args": {"command": "echo hi"}}',
                allowed_schemas={"shell_exec": ShellCmd},
            ),
            tmp_path,
            vfs=vfs,
            on_step=cancel,
        )


# ---------------------------------------------------------------------------
# 元素 23: evidence_refine
# ---------------------------------------------------------------------------


async def test_evidence_refine_dedupe_rank_cap(tmp_path):
    raw = [
        {"source": "a", "content": "same", "confidence": 0.3},
        {"source": "b", "content": "same", "confidence": 0.9},  # 与上一条内容重复
        {"source": "c", "content": "unique", "confidence": 0.7},
        {"source": "d", "content": "low", "confidence": 0.1},
    ]
    config = EvidenceConfig(max_findings=2)
    result = await evidence_refine_workflow(
        config,
        EvidenceInput(anon_session_ref="anon-1", raw_evidence=raw),
        tmp_path,
    )
    assert result["status"] == "completed"
    kept = result["findings"]["evidence"]
    assert result["findings"]["total"] == 3  # 去重后
    assert len(kept) == 2  # 截断
    assert kept[0]["content"] == "unique"  # 置信度降序


async def test_evidence_refine_fingerprint_stable(tmp_path):
    raw = [{"source": "a", "content": "x", "confidence": 1.0}]
    r1 = await evidence_refine_workflow(
        EvidenceConfig(),
        EvidenceInput(anon_session_ref="anon-1", source_hint="s1", raw_evidence=raw),
        tmp_path,
    )
    r2 = await evidence_refine_workflow(
        EvidenceConfig(),
        EvidenceInput(anon_session_ref="anon-1", source_hint="s1", raw_evidence=raw),
        tmp_path,
    )
    assert r1["fingerprint"] == r2["fingerprint"]
    assert len(r1["fingerprint"]) == 24


# ---------------------------------------------------------------------------
# 元素 24: session_tree_mgr
# ---------------------------------------------------------------------------


async def test_session_tree_upsert_with_parent(tmp_path):
    store = SQLiteKVStore(str(tmp_path / "tree.db"))
    root = await session_tree_upsert_workflow(
        SessionTreeConfig(),
        SessionNodeInput(anon_session_ref="anon-1", node_key="root", payload={"kind": "root"}),
        tmp_path,
        store=store,
    )
    assert root["status"] == "completed"
    assert root["findings"]["tree_path"] == "sessions/anon-1/nodes/root"

    child = await session_tree_upsert_workflow(
        SessionTreeConfig(),
        SessionNodeInput(
            anon_session_ref="anon-1",
            node_key="child",
            parent_key="root",
            payload={"kind": "child"},
        ),
        tmp_path,
        store=store,
    )
    assert child["status"] == "completed"
    saved = await store.get("sessions/anon-1/nodes/child")
    assert saved["parent_key"] == "root"


async def test_session_tree_missing_parent_fails_swallowed(tmp_path):
    store = SQLiteKVStore(str(tmp_path / "tree.db"))
    result = await session_tree_upsert_workflow(
        SessionTreeConfig(),
        SessionNodeInput(
            anon_session_ref="anon-1", node_key="orphan", parent_key="ghost", payload={}
        ),
        tmp_path,
        store=store,
    )
    assert result["status"] == "failed"
    assert result["error"]["type"] == "KeyError"
