"""3O 范式全栈 — omodul 业务事务: 会话树节点管理 (元素 24).

经 oprim 原子 (put_state/fetch_state) 持久化会话树节点;
不感知树结构之外的业务语义。节点键: sessions/{anon_ref}/nodes/{key}。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from obase import KVStoreProvider, OmodulBaseConfig
from oprim import fetch_state, put_state
from pydantic import BaseModel


class SessionTreeConfig(OmodulBaseConfig):
    _omodul_name = "session_tree_mgr"
    _omodul_version = "1.0.0"
    _enabled_pillars = {"decision_trail", "fingerprint"}
    _fingerprint_fields = {"anon_session_ref", "node_key"}


class SessionNodeInput(BaseModel):
    anon_session_ref: str
    node_key: str
    parent_key: str | None = None
    payload: dict[str, Any]


def compute_fingerprint_for(config: SessionTreeConfig, input_data: SessionNodeInput) -> str:
    """暴露给服务层用于防重/幂等判断 (规范 §5.5)"""
    fingerprint_dict = {
        k: getattr(input_data, k)
        for k in config._fingerprint_fields
        if hasattr(input_data, k)
    }
    raw_str = json.dumps(fingerprint_dict, sort_keys=True)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:24]


async def _write_trail_async(output_dir: Path, trail: list) -> None:
    """辅助函数：将审计轨迹落盘"""
    trail_path = output_dir / "decision_trail.json"
    with open(trail_path, "w") as f:
        json.dump(trail, f, indent=2)


async def session_tree_upsert_workflow(
    config: SessionTreeConfig,
    input_data: SessionNodeInput,
    output_dir: Path,
    *,
    store: KVStoreProvider,
    on_step: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """业务事务: 校验父节点 -> 持久化会话树节点。

    Composes oprim: [fetch_state, put_state]
    """
    decision_trail = []
    status = "completed"
    error = None
    findings = {}

    tree_key = f"sessions/{input_data.anon_session_ref}/nodes/{input_data.node_key}"

    try:
        if on_step:
            on_step({"step": 1, "msg": "resolve parent"})

        parent_key = None
        if input_data.parent_key:
            # 父节点必须存在 (fetch_state 缺失时抛 KeyError, 触发 failed 分支)
            await fetch_state(
                f"sessions/{input_data.anon_session_ref}/nodes/{input_data.parent_key}",
                store=store,
            )
            parent_key = input_data.parent_key
            decision_trail.append({"action": "parent_resolved", "parent": parent_key})

        if on_step:
            on_step({"step": 2, "msg": "persist node"})

        await put_state(
            tree_key,
            value={
                "node_key": input_data.node_key,
                "parent_key": parent_key,
                "payload": input_data.payload,
            },
            store=store,
        )
        findings = {"tree_path": tree_key}
        decision_trail.append({"action": "upsert", "key": tree_key, "status": "success"})

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
