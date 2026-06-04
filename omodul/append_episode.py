"""omodul.append_episode —— 追加一条 Episode 到 Episodic Memory（SPEC-3）。

3O 层级：omodul（构造 episode + 写入 + 业务事务语义）。
13-Learning 回流飞轮的**唯一合法入口**（基线裁决2）。双维度进化的入口。

支柱：_enabled_pillars = {decision_trail}
    decision_trail —— Episode 是回流料源，来源必须可追溯。
    不启 fingerprint —— Episode 是流水事件，不去重。

强制校验（13-Learning）：
    project_id 非空（R5 非孤儿）；env_fingerprint 必含（R1/R2 复现/归因依赖）。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import ConfigDict

from omodul._base_config import BaseConfig

# ---------------------------------------------------------------------------
# 本地异常（替代 aii.errors 导入）
# ---------------------------------------------------------------------------


class MemoryOrphanedError(Exception):
    """Episode/节点缺少必要关联（如 project_id 为空）。"""

    code = "ERR_MEMORY_ORPHANED"

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class AppendEpisodeConfig(BaseConfig):
    """append_episode 配置."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    _omodul_name: ClassVar[str] = "append_episode"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = set()

    backend: Any  # StorageBackend — arbitrary type allowed


# 该 omodul 启用的四支柱子集（3O §5.3，显式声明）
_enabled_pillars: set[str] = {"decision_trail"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def append_episode(
    config: AppendEpisodeConfig,
    input_data: dict,
    output_dir: str | None = None,
    *,
    on_step: Callable | None = None,
) -> dict:
    """标准 omodul 签名 → dict。

    config:     AppendEpisodeConfig（含 backend）
    input_data: {"episode": {project_id, event, outcome, env_fingerprint, ...}}
    output_dir: decision_trail.json 落盘目录（None 则不落盘，仍返回 trail）
    on_step:    每步回调（可选）

    返回：findings=episode_id / status / error / decision_trail / report_path / cost_usd

    回流唯一入口：不提供绕过本 omodul 直写 Episodic 的旁路。
    不在此做提炼（提炼是 M2/M3 的事）。失败不 raise。
    """
    if isinstance(config, dict):
        config = AppendEpisodeConfig(**config) if config else AppendEpisodeConfig()
    trail: list[dict] = []
    episode_id = None
    status = "failed"
    error = None

    def _emit(step_dict: dict) -> None:
        trail.append(step_dict)
        if on_step is not None:
            on_step(step_dict)

    try:
        backend = config.backend
        ep = input_data["episode"]

        # 校验 1：非孤儿（project_id 非空）—— 13-Learning R5
        project_id = ep.get("project_id")
        step = {"step": "check_non_orphan", "project_id": project_id}
        _emit(step)
        if not project_id:
            raise MemoryOrphanedError(
                "episode missing project_id (orphan not allowed)",
                detail={"reason": "no_project"},
            )
        trail[-1]["result"] = "pass"

        # 校验 2：env_fingerprint 必含 —— R1/R2 复现/归因依赖
        env_fp = ep.get("env_fingerprint")
        step2 = {"step": "check_env_fingerprint", "present": bool(env_fp)}
        _emit(step2)
        if not env_fp:
            raise MemoryOrphanedError(
                "episode missing env_fingerprint",
                detail={"reason": "no_env_fingerprint"},
            )
        trail[-1]["result"] = "pass"

        # 构造合格 Episode（HMS schema）
        episode_id = ep.get("episode_id") or f"EP-{uuid.uuid4()}"
        stored_episode = {
            "episode_id": episode_id,
            "type": "Episode",
            "project": project_id,
            "event": ep.get("event"),
            "outcome": ep.get("outcome"),
            "context": {"env_fingerprint": env_fp, **ep.get("context", {})},
            "human_verdict": ep.get("human_verdict"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # 写入 Episodic（M0 用同一图存储，Episode 作为一类节点）
        backend.put_node(episode_id, stored_episode)
        # 非孤儿落地：建一条 belongs_to 边连到 project
        backend.put_edge(episode_id, "belongs_to", project_id)
        _emit({"step": "store_episode", "episode_id": episode_id, "result": "ok"})

        status = "completed"
    except MemoryOrphanedError as e:
        error = {"code": e.code, "message": e.message, "detail": e.detail}
        _emit({"step": "abort", "error": error})
    except Exception as e:
        error = {"code": "ERR_UNEXPECTED", "message": str(e)}
        _emit({"step": "abort", "error": error})
    finally:
        decision_trail = {
            "omodul": "append_episode",
            "enabled_pillars": sorted(_enabled_pillars),
            "episode_id": episode_id,
            "status": status,
            "steps": trail,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            with open(os.path.join(output_dir, "decision_trail.json"), "w", encoding="utf-8") as f:
                json.dump(decision_trail, f, ensure_ascii=False, indent=2)

    return {
        "findings": episode_id if status == "completed" else None,
        "status": status,
        "error": error,
        "decision_trail": decision_trail,
        "report_path": None,
        "cost_usd": 0.0,
    }
