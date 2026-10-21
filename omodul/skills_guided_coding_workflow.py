"""omodul.skills_guided_coding_workflow — 动态 Rules 挂载与领域 Check 的专业编程事务.

Pillars: fingerprint, decision_trail, report, cost

签名: (config, input_data, output_dir, *, on_step=None)
失败绝对不 raise — 返回 status="failed".

遵循 omodul 红线:
- 显式声明 _enabled_pillars
- 含 "failed" 状态返回
- 禁止 try 之外裸 raise
- 禁止 sibling omodul 导入
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class Config(BaseConfig):
    """Skills Guided Coding Workflow 配置."""

    _omodul_name: ClassVar[str] = "skills_guided_coding_workflow"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {
        "fingerprint",
        "decision_trail",
        "report",
        "cost",
    }

    inject_sk: Any = None
    rule_check_op: Any = None
    skills_registry: Any = None
    llm_caller: Any = None


class InputData(BaseModel):
    """Skills Guided Coding Workflow 输入."""

    file_path: str = "contract.rs"
    code_content: str = "fn process() {}"
    domain: str = ""

    model_config = {"arbitrary_types_allowed": True}


def skills_guided_coding_workflow(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """动态 Rules 挂载 + 领域 Rule Check 的专业编程业务事务.

    流程:
    1. 从 SkillsRegistry 匹配适用的编码规则
    2. LLM 合成规则注入前缀
    3. 领域特定静态代码安全检查

    Args:
        config: 配置（含注入的 inject_sk / rule_check_op / registry / llm_caller）
        input_data: 输入数据
        output_dir: 输出目录
        on_step: 步骤回调（可选）

    Returns:
        {"status": "completed"|"violations_found"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []

    inject_sk = config.inject_sk
    rule_check_op = config.rule_check_op
    registry = config.skills_registry
    llm_caller = config.llm_caller

    file_path = input_data.file_path
    code_content = input_data.code_content

    try:
        # Phase 1: 动态注入扩展名 Skills/Rules
        trail.record(event="skills_inject", file_path=file_path)
        if inject_sk is not None and registry is not None and llm_caller is not None:
            inject_res = inject_sk(file_path, registry=registry, llm_caller=llm_caller)
        else:
            inject_res = {"matched_count": 0, "rules": [], "injected_prompt_prefix": ""}

        decision_trail.append({
            "step": "skills_injected",
            "matched_count": inject_res.get("matched_count", 0),
            "rules": inject_res.get("rules", []),
        })

        # Phase 2: 领域特定 Guardrail 静态检查
        trail.record(event="domain_rule_check", file_path=file_path)
        domain = input_data.domain or _infer_domain(file_path)
        if rule_check_op is not None:
            check_res = rule_check_op(code_content, domain=domain)
        else:
            check_res = {"domain": domain, "is_compliant": True, "violations": []}

        decision_trail.append({
            "step": "domain_rule_check",
            "domain": domain,
            "compliant": check_res.get("is_compliant", True),
            "violations_count": len(check_res.get("violations", [])),
        })

        cost.add(in_tok=500, out_tok=300)

        # 生成报告
        report_content = (
            f"# Skills Guided Coding Report\n\n"
            f"## File: {file_path}\n"
            f"## Domain: {domain}\n\n"
            f"### Injected Rules ({inject_res.get('matched_count', 0)}):\n"
            + "\n".join(f"- {r}" for r in inject_res.get("rules", []))
            + "\n\n### Rule Check Results:\n"
            f"- Compliant: {check_res.get('is_compliant')}\n"
            + "\n".join(
                f"- [{v['severity']}] {v['rule']}"
                for v in check_res.get("violations", [])
            )
        )
        report_path = write_report(
            report_content, output_dir=output_dir,
            name=f"skills_guided_{Path(file_path).stem}",
            fmt="markdown",
        )

        trail.write(output_dir)

        final_status = "completed" if check_res.get("is_compliant") else "violations_found"

        return build_result(
            status=final_status,
            error=None,
            cost_usd=cost.total_usd,
            report_path=str(report_path),
            trail=trail,
            trail_path=trail.write(output_dir),
            summary=f"Skills guided coding finished for: {file_path}",
            pillars={
                "fingerprint": {"file": file_path, "domain": domain},
                "decision_trail": decision_trail,
                "report": {
                    "rule_check": check_res,
                    "injected_rules": inject_res.get("rules"),
                    "injected_prefix": inject_res.get("injected_prompt_prefix"),
                },
                "cost": {"tokens_used": 850, "cost_usd": cost.total_usd},
            },
        )

    except Exception as e:
        return build_result(
            status="failed",
            error={"type": type(e).__name__, "message": str(e)},
            cost_usd=cost.total_usd,
            trail=trail,
            summary=f"Skills Guided Coding Workflow failed: {str(e)}",
            pillars={"decision_trail": decision_trail, "report": {"error": str(e)}},
        )


def _infer_domain(file_path: str) -> str:
    """从文件路径推导领域."""
    p = file_path.lower()
    if ".rs" in p or "solana" in p:
        return "solana"
    if ".sol" in p:
        return "ethereum"
    if ".rs" in p and "solana" not in p:
        return "rust"
    return "security"
