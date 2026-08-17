"""omodul.phase_spec_driven_plan — Spec Kit files → validated DAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from obase.veya_workspace import SpecKitPaths
from omodul._base import BaseConfig, Trail, build_result, compute_fingerprint
from oprim._speckit_io import load_speckit_artifacts, save_taskgraph
from oskill.dag_compiler import compile_spec_to_dag, validate_taskgraph_dag


class SpecPlanConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "phase_spec_driven_plan"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "fingerprint"}
    _fingerprint_fields: ClassVar[set[str]] = {"goal_id"}

    goal_id: str
    project_root: Path
    max_leaf_tasks: int = 40


class SpecPlanInput(BaseModel):
    model_config = ConfigDict(extra="allow")


def compute_fingerprint_for(config: SpecPlanConfig, input_data: SpecPlanInput) -> str:
    return compute_fingerprint({"goal_id": config.goal_id, "root": str(config.project_root)})


async def phase_spec_driven_plan(
    config: SpecPlanConfig | dict[str, Any],
    input_data: SpecPlanInput | dict[str, Any],
    output_dir: Path,
    *,
    on_step=None,
) -> dict[str, Any]:
    trail = Trail()
    if not isinstance(config, SpecPlanConfig):
        config = SpecPlanConfig.model_validate(config)
    if not isinstance(input_data, SpecPlanInput):
        input_data = SpecPlanInput.model_validate(input_data or {})
    fp = compute_fingerprint_for(config, input_data)
    paths = SpecKitPaths(config.project_root)
    try:
        artifacts = await load_speckit_artifacts(
            paths, artifact_types=["constitution.md", "tasks.md"]
        )
        trail.record(event="artifacts_loaded", keys=sorted(artifacts))
        if "tasks.md" not in artifacts or "constitution.md" not in artifacts:
            return build_result(
                status="failed",
                error={
                    "type": "MissingSpecKit",
                    "message": "Missing Spec Kit artifacts. Run /speckit first.",
                },
                trail=trail,
                fingerprint=fp,
                findings={},
            )
        nodes = compile_spec_to_dag(artifacts["tasks.md"])
        errors = validate_taskgraph_dag(nodes, max_leaf_tasks=config.max_leaf_tasks)
        if errors:
            trail.record(event="dag_compilation_failed", details=errors)
            return build_result(
                status="failed",
                error={
                    "type": "InvalidDAG",
                    "message": "Invalid DAG generated from tasks.md",
                    "details": errors,
                },
                trail=trail,
                fingerprint=fp,
                findings={"errors": errors},
            )
        graph = {
            "version": 1,
            "goal_id": config.goal_id,
            "constitution": artifacts["constitution.md"],
            "tasks": [n.model_dump() for n in nodes],
        }
        dest = await save_taskgraph(paths, goal_id=config.goal_id, graph_dict=graph)
        trail.record(event="dag_compiled", task_count=len(nodes), path=str(dest))
        if on_step:
            on_step({"task_count": len(nodes)})
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            trail.write(Path(output_dir))
        return build_result(
            status="completed",
            error=None,
            trail=trail,
            fingerprint=fp,
            findings={
                "tasks": [n.model_dump() for n in nodes],
                "constitution": artifacts["constitution.md"],
                "taskgraph_path": str(dest),
            },
        )
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            fingerprint=fp,
            findings={},
        )
