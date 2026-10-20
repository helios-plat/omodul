"""omodul.agent_self_update_workflow — Agent 配置与 SOUL 自进化更新审计事务.

集成：oskill.prompt_evolution_eval（变体评估）+ oprim.file_write_atomic
（原子落盘）+ obase.versionstore.VersionStore（更新前快照审计）。

Pillars: fingerprint, decision_trail
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint


class AgentSelfUpdateConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "agent_self_update_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {"config_path"}
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail"}

    min_score: float = 6.0  # 低于该分不采纳候选


class AgentSelfUpdateInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    config_path: str
    current_content: str
    candidate_content: str
    eval_samples: list[str] = ["任务：总结一段代码", "任务：规划一次重构"]
    judge: Any = None  # prompt_evolution_eval 的 judge LLM（None = 跳过评估直接更新）


def compute_fingerprint_for(config: AgentSelfUpdateConfig, input_data: AgentSelfUpdateInput) -> str:
    """Fingerprint over config_path + 内容哈希。"""
    import hashlib

    return compute_fingerprint(
        {
            "config_path": input_data.config_path,
            "sha": hashlib.sha256(input_data.candidate_content.encode()).hexdigest()[:16],
        }
    )


async def agent_self_update_workflow(
    config: AgentSelfUpdateConfig,
    input_data: AgentSelfUpdateInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """评估候选配置并原子更新，全程留审计轨迹。失败绝不 raise。"""
    trail = Trail()
    fp = compute_fingerprint_for(config, input_data)

    try:
        from obase.versionstore import VersionStore
        from oprim import file_write_atomic

        target = Path(input_data.config_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        store = VersionStore(Path(output_dir) / "versionstore")

        # 更新前快照（审计）
        rev_before = (
            store.snapshot([target], message="agent_self_update:before")
            if target.exists()
            else None
        )
        trail.record(event="snapshot_before", rev=rev_before)

        adopted = False
        eval_result: dict[str, Any] = {}
        if input_data.judge is not None:
            if on_step:
                on_step({"stage": "evaluate", "status": "running"})
            from oskill import prompt_evolution_eval

            eval_result = await prompt_evolution_eval(
                {"current": input_data.current_content, "candidate": input_data.candidate_content},
                samples=input_data.eval_samples,
                judge=input_data.judge,
            )
            candidate_avg = eval_result["scores"]["candidate"]["avg"]
            current_avg = eval_result["scores"]["current"]["avg"]
            trail.record(
                event="variants_evaluated",
                current_avg=current_avg,
                candidate_avg=candidate_avg,
                best=eval_result["best_variant"],
            )
            adopted = candidate_avg >= config.min_score and candidate_avg >= current_avg
        else:
            adopted = True  # 无 judge：直接采纳（显式声明）

        if not adopted:
            return build_result(
                status="completed",
                error=None,
                fingerprint=fp,
                trail=trail,
                trail_path=trail.write(Path(output_dir)) if output_dir else None,
                adopted=False,
                eval_result=eval_result,
            )

        written = await file_write_atomic(
            target, content=input_data.candidate_content, overwrite=True
        )
        rev_after = store.snapshot([target], message="agent_self_update:after")
        trail.record(
            event="config_updated", path=str(target),
            rev_after=rev_after, bytes_written=written["bytes_written"],
        )

        if on_step:
            on_step({"stage": "updated", "status": "completed", "path": str(target)})

        return build_result(
            status="completed",
            error=None,
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            adopted=True,
            path=str(target),
            rev_before=rev_before,
            rev_after=rev_after,
            eval_result=eval_result,
        )

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            fingerprint=fp,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
        )
