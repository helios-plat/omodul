"""3O 范式内化 — Graft 上下文构建流水线 (omodul).

扫描工作区 → 比对 SHA256 缓存 (0 成本跳过) → 解析 AST →
一句话摘要 (LLM 注入) → 落盘 Markdown 图谱节点。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from obase import OmodulBaseConfig
from obase.graph_storage import GraphNode, GraphRelation, MarkdownGraphStore
from oprim import crux_hash, parse_symbols
from oskill import summarize_symbol
from oskill.protocols import LLMCaller
from pydantic import BaseModel


class ContextBuilderConfig(OmodulBaseConfig):
    _omodul_name = "context_builder"
    _omodul_version = "1.0.0"
    _enabled_pillars = {"decision_trail", "fingerprint"}
    _fingerprint_fields = {"anon_task_ref", "workspace_dir"}


class ContextInput(BaseModel):
    anon_task_ref: str
    workspace_dir: str
    files: list[str]
    language: str = "python"


def compute_fingerprint_for(config: ContextBuilderConfig, input_data: ContextInput) -> str:
    fingerprint_dict = {
        k: getattr(input_data, k)
        for k in config._fingerprint_fields
        if hasattr(input_data, k)
    }
    raw_str = json.dumps(fingerprint_dict, sort_keys=True)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:24]


async def _write_trail_async(output_dir: Path, trail: list) -> None:
    trail_path = output_dir / "decision_trail.json"
    with open(trail_path, "w") as f:
        json.dump(trail, f, indent=2)


async def build_context_workflow(
    config: ContextBuilderConfig,
    input_data: ContextInput,
    output_dir: Path,
    *,
    llm: LLMCaller,
    on_step: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """业务事务: 增量构建 Graft 上下文图谱 (Hash 未变 → 0 成本跳过)。

    Composes oprim: [parse_symbols, crux_hash]
    Composes oskill: [summarize_symbol]
    """
    decision_trail = []
    status = "completed"
    error = None
    findings = {}

    workspace = Path(input_data.workspace_dir)
    store = MarkdownGraphStore(workspace / ".veya" / "graph")

    try:
        if on_step:
            on_step({"step": 1, "msg": "scan workspace hashes"})

        changed: list[str] = []
        skipped = 0
        for rel in input_data.files:
            path = workspace / rel
            if not path.exists():
                continue
            raw = path.read_bytes()
            content_hash = MarkdownGraphStore.hash_content(raw)
            node_id = Path(rel).stem
            if store.check_hash(node_id, content_hash):
                skipped += 1
                continue
            changed.append(rel)

        if on_step:
            on_step({"step": 2, "msg": f"build {len(changed)} nodes"})

        built: list[str] = []
        for rel in changed:
            path = workspace / rel
            raw = path.read_bytes()
            code = raw.decode("utf-8", errors="replace")
            content_hash = MarkdownGraphStore.hash_content(raw)
            node_id = Path(rel).stem

            # 解析 AST 提取符号网络 (同步确定性原语)
            symbols = parse_symbols(code, language=input_data.language)
            # Crux 切片 (行号无关的稳定代码段)
            crux = crux_hash(code)
            # 一句话摘要 (仅新增/变更节点触发 LLM)
            summary = await summarize_symbol(node_id, code=crux or code[:1500], llm=llm)

            # 依赖关系 (import 符号 → depends_on 边)
            relations = []
            for s in symbols:
                if s["kind"] == "import":
                    target = s["name"].split(".")[0]
                    if target and target != node_id:
                        relations.append({"target": target, "type": "depends_on"})

            node = GraphNode(
                node_id=node_id,
                file_path=rel,
                content_hash=content_hash,
                summary=summary,
                crux=crux,
                relations=[
                    GraphRelation(target=r["target"], type=r["type"]) for r in relations[:8]
                ],
            )
            store.save_node(node)
            built.append(node_id)
            decision_trail.append({"action": "build_node", "node": node_id})

        findings = {"built": built, "skipped": skipped, "total": len(input_data.files)}
        decision_trail.append({"action": "done", "built": len(built), "skipped": skipped})

        if on_step:
            on_step({"step": 3, "msg": "pipeline done"})

    except asyncio.CancelledError:
        status = "cancelled"
        error = {"reason": "Task was cancelled or timed out"}
        decision_trail.append({"action": "cancelled"})
        raise

    except Exception as e:
        status = "failed"
        error = {"type": type(e).__name__, "message": str(e)}
        decision_trail.append({"action": "error", "error": error})

    finally:
        if "decision_trail" in config._enabled_pillars:
            output_dir.mkdir(parents=True, exist_ok=True)
            await asyncio.shield(_write_trail_async(output_dir, decision_trail))

    return {
        "status": status,
        "error": error,
        "findings": findings,
        "fingerprint": compute_fingerprint_for(config, input_data)
        if "fingerprint" in config._enabled_pillars
        else None,
        "cost_usd": 0.0,
    }
