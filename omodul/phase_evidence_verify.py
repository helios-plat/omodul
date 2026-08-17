"""omodul.phase_evidence_verify — G2 QA on worker git_diff + stdout."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from oprim.boss_llm_callers import call_llm_for_verification
from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, CostTracker, Trail, build_result

_QA_SYSTEM = (
    "你是无情的 QA 检查官。对照 acceptance，严格审查 worker 提交的"
    "实际 git diff 与执行结果。只输出 JSON："
    "{\"passed\": true|false, \"reasoning\": \"...\"}。"
    "证据不足或与 acceptance 不符必须 failed。"
)


class EvidenceVerifyConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "phase_evidence_verify"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "cost"}


class EvidenceVerifyInput(BaseModel):
    task: Any = None
    leaf_result: dict[str, Any] = {}
    llm_caller: Any = None

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


async def phase_evidence_verify(
    config: EvidenceVerifyConfig | dict[str, Any],
    input_data: EvidenceVerifyInput | dict[str, Any],
    output_dir: Path,
    *,
    on_step=None,
) -> dict[str, Any]:
    """G2: acceptance vs git_diff/stdout. Failures carry correction_instruction."""
    trail = Trail()
    cost = CostTracker()
    if not isinstance(config, EvidenceVerifyConfig):
        config = EvidenceVerifyConfig.model_validate(config or {})
    data = (
        input_data
        if isinstance(input_data, EvidenceVerifyInput)
        else EvidenceVerifyInput.model_validate(input_data or {})
    )
    try:
        if data.leaf_result.get("status") == "blocked":
            return build_result(
                status="completed",
                error=None,
                trail=trail,
                findings={"passed": False, "summary": "leaf blocked"},
            )
        caller = data.llm_caller
        if caller is None:
            extra = getattr(data, "__pydantic_extra__", None) or {}
            caller = extra.get("llm_caller")
        if caller is None:
            return build_result(
                status="failed",
                error={"type": "MissingLLMCaller", "message": "llm_caller is required"},
                trail=trail,
                findings={"passed": False},
            )
        acceptance = _field(data.task, "acceptance")
        if isinstance(acceptance, str):
            acceptance = [acceptance]
        acc_text = "\n".join(f"- {item}" for item in (acceptance or []) if item)
        leaf = data.leaf_result or {}
        git_diff = str(leaf.get("git_diff") or "None")
        stdout = str(leaf.get("stdout") or leaf.get("brief") or leaf.get("summary") or "")
        messages = [
            {"role": "system", "content": _QA_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Acceptance 准则:\n{acc_text or '(empty)'}\n\n"
                    f"Worker 实际 Git Diff:\n{git_diff}\n\n"
                    f"Worker Stdout:\n{stdout}"
                ),
            },
        ]
        judgment = await call_llm_for_verification(messages, caller=caller)
        cost.add_from_response(judgment)
        passed = bool(judgment.get("passed"))
        reasoning = str(judgment.get("reasoning") or "")
        findings: dict[str, Any] = {"passed": passed, "summary": reasoning}
        if not passed:
            findings["correction_instruction"] = (
                f"[RETRY FEEDBACK] 验收未通过。原因: {reasoning}。"
                "请修正代码逻辑后重试。不要扩大范围。"
            )
            trail.record(event="verify_failed", reason=reasoning)
        else:
            trail.record(event="verify_passed")
        if on_step:
            on_step({"passed": passed})
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            trail.write(Path(output_dir))
        return build_result(
            status="completed",
            error=None,
            trail=trail,
            findings=findings,
            cost_usd=cost.total_usd,
        )
    except Exception as exc:
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            findings={"passed": False},
            cost_usd=cost.total_usd,
        )


def _field(task: Any, name: str) -> Any:
    if task is None:
        return None
    if isinstance(task, dict):
        return task.get(name)
    return getattr(task, name, None)
