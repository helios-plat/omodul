"""omodul.phase_verify_leaf_task — check acceptance against a leaf log."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, Trail, build_result


class VerifyConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "phase_verify_leaf_task"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail"}


class VerifyInput(BaseModel):
    execution_log: str = ""
    acceptance: list[str] = []
    leaf_status: str = ""

    model_config = ConfigDict(extra="allow")


async def phase_verify_leaf_task(
    config: VerifyConfig | dict[str, Any],
    input_data: VerifyInput | dict[str, Any],
    output_dir: Path,
    *,
    on_step=None,
) -> dict[str, Any]:
    trail = Trail()
    if not isinstance(config, VerifyConfig):
        config = VerifyConfig.model_validate(config or {})
    data = (
        input_data
        if isinstance(input_data, VerifyInput)
        else VerifyInput.model_validate(input_data or {})
    )
    try:
        if data.leaf_status == "blocked":
            return build_result(
                status="completed",
                error=None,
                trail=trail,
                findings={"passed": False, "reason": "leaf blocked"},
            )
        missing = [
            rule
            for rule in data.acceptance
            if rule and rule.lower() not in data.execution_log.lower()
        ]
        trail.record(event="verified", missing=missing)
        if on_step:
            on_step({"missing": missing})
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            trail.write(Path(output_dir))
        passed = not missing
        return build_result(
            status="completed",
            error=None,
            trail=trail,
            findings={"passed": passed, "missing": missing},
        )
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            findings={"passed": False},
        )
