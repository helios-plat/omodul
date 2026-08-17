"""omodul.implicit_feedback_processor — v0/v1 AST → knowledge or experience."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

from oprim._extract_ast_delta import extract_ast_delta
from oprim._write_fact_node import write_fact_node
from oskill.ast_diffing import compute_ast_diff
from oskill.intent_categorization import categorize_diff_intent
from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, CostTracker, Trail, build_result, compute_fingerprint


class FeedbackConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "implicit_feedback_processor"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "cost"}
    _fingerprint_fields: ClassVar[set[str]] = {"repo_path", "file_path", "v1_commit"}

    repo_path: Path
    file_path: Path
    v0_commit: str = ""
    v1_commit: str = ""


class FeedbackInput(BaseModel):
    entity_id: str = ""
    llm_caller: Any = None
    graph_pool: Any = None

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")


def compute_fingerprint_for(config: FeedbackConfig, input_data: FeedbackInput) -> str:
    return compute_fingerprint(
        {
            "repo_path": str(config.repo_path),
            "file_path": str(config.file_path),
            "v1_commit": config.v1_commit,
        }
    )


async def implicit_feedback_processor(
    config: FeedbackConfig | dict[str, Any],
    input_data: FeedbackInput | dict[str, Any],
    output_dir: Path,
    *,
    on_step=None,
) -> dict[str, Any]:
    trail = Trail()
    cost = CostTracker()
    if not isinstance(config, FeedbackConfig):
        config = FeedbackConfig.model_validate(config)
    if not isinstance(input_data, FeedbackInput):
        input_data = FeedbackInput.model_validate(input_data)
    fp = compute_fingerprint_for(config, input_data)
    findings: dict[str, Any] = {}
    try:
        ast_v0, ast_v1 = await asyncio.gather(
            extract_ast_delta(
                config.repo_path,
                target_file=config.file_path,
                commit_hash=config.v0_commit,
            ),
            extract_ast_delta(
                config.repo_path,
                target_file=config.file_path,
                commit_hash=config.v1_commit,
            ),
        )
        trail.record(event="ast_extracted", v0=ast_v0.get("ok"), v1=ast_v1.get("ok"))
        if on_step:
            on_step({"step": "ast_extracted"})
        if not ast_v0.get("ok") or not ast_v1.get("ok"):
            return build_result(
                status="failed",
                error={
                    "type": "AstExtractFailed",
                    "message": ast_v0.get("error") or ast_v1.get("error") or "extract failed",
                },
                trail=trail,
                fingerprint=fp,
                findings={"action": "extract_failed"},
            )

        diff_struct = compute_ast_diff(ast_v0, ast_v1)
        trail.record(event="diffed", meaningful=diff_struct.get("has_meaningful_change"))
        if not diff_struct.get("has_meaningful_change"):
            findings = {"action": "ignored_style_noise", "diff": diff_struct}
            return _done(trail, fp, cost, findings, output_dir)

        intent = await categorize_diff_intent(diff_struct, llm_caller=input_data.llm_caller)
        trail.record(event="intent", category=intent.get("category"))
        if on_step:
            on_step({"step": "intent", "category": intent.get("category")})

        entity = input_data.entity_id or Path(config.file_path).as_posix()
        if intent.get("category") == "NEW_KNOWLEDGE":
            if input_data.graph_pool is None:
                findings = {"action": "knowledge_graph_skipped", "reason": "no graph_pool"}
            else:
                node_id = await write_fact_node(
                    entity,
                    predicate="ast_delta",
                    object_val=str(diff_struct.get("added")),
                    evidence_chunk=f"{config.v0_commit}->{config.v1_commit}:{config.file_path}",
                    pool=input_data.graph_pool,
                )
                findings = {"action": "knowledge_graph_updated", "node_id": node_id}
        else:
            if input_data.graph_pool is not None:
                from obase.graph_store.models import ExperienceItem

                await input_data.graph_pool.append_experience(
                    ExperienceItem(
                        item_id=uuid4().hex,
                        file_path=str(config.file_path),
                        category="LOGIC_CORRECTION",
                        summary=str(diff_struct.get("added")),
                    )
                )
            findings = {"action": "experience_pool_appended", "intent": intent}

        return _done(trail, fp, cost, findings, output_dir)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            fingerprint=fp,
            findings=findings,
            cost_usd=cost.total_usd,
        )


def _done(
    trail: Trail,
    fp: str,
    cost: CostTracker,
    findings: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        trail.write(Path(output_dir))
    return build_result(
        status="completed",
        error=None,
        trail=trail,
        fingerprint=fp,
        findings=findings,
        cost_usd=cost.total_usd,
    )
