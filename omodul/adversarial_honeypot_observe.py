"""omodul.adversarial_honeypot_observe — O3 反间谍蜜罐观测事务.

Pillars: fingerprint, decision_trail, report

签名: (config, input_data, output_dir, *, on_step=None)
失败绝对不 raise — 返回 status="failed".

O3 变体: 沙箱前瞻 + 蜜罐凭证注入 + 异常检测。
在拉起沙箱前，向环境中注入伪造的高敏凭证。
如果探针检测到异常访问行为，立即终止并生成 escalation 报告。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class Config(BaseConfig):
    """Adversarial Honeypot Observe 配置."""

    _omodul_name: ClassVar[str] = "adversarial_honeypot_observe"
    _omodul_version: ClassVar[str] = "1.1.0"
    _enabled_pillars: ClassVar[set[str]] = {"fingerprint", "decision_trail", "report"}

    sandbox_op: Any = None
    llm_caller: Any = None

    # 蜜罐配置
    honeypot_vars: dict[str, str] = {}
    forbidden_network_patterns: list[str] = []
    max_probe_timeout: int = 10


class InputData(BaseModel):
    """Adversarial Honeypot Observe 输入."""

    probe_command: str = "echo 'sandbox probe'"
    state_id: str = "honeypot_test"
    context: str = ""

    model_config = {"arbitrary_types_allowed": True}


# 默认蜜罐凭证
_DEFAULT_HONEYPOT_VARS: dict[str, str] = {
    "DB_PASS_HONEYPOT": "fake_db_password_2024_honeypot",
    "AWS_SECRET_HONEYPOT": "fake_aws_secret_key_honeypot",
    "API_KEY_HONEYPOT": "fake_api_key_sk_live_honeypot",
    "JWT_SECRET_HONEYPOT": "fake_jwt_signing_key_do_not_use",
}

# 禁止的网络请求模式
_DEFAULT_FORBIDDEN_PATTERNS: list[str] = [
    r"curl\s+.*\.exfil\.com",
    r"wget\s+.*\.evil\.net",
    r"nc\s+.*\.\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
    r"dig\s+.*\.beacon\.",
    r"python.*requests.*\.post\(.*exfil",
    r"ssh\s+.*@.*\.unknown-host\.",
]


def adversarial_honeypot_observe(
    config: Config,
    input_data: InputData,
    output_dir: Path,
    *,
    on_step: Any = None,
) -> dict[str, Any]:
    """O3 反间谍蜜罐观测事务.

    流程:
    1. 将蜜罐凭证注入沙箱环境变量
    2. 执行探针命令
    3. 检测输出中是否有蜜罐访问痕迹
    4. 检测是否有异常网络请求
    5. 若检测到异常 → escalation

    Args:
        config: 配置（含 sandbox_op / honeypot_vars / forbidden_network_patterns）
        input_data: 输入数据
        output_dir: 输出目录
        on_step: 步骤回调

    Returns:
        {"status": "completed"|"escalated"|"failed", ...}
    """
    cost = CostTracker()
    trail = Trail()
    decision_trail: list[dict[str, Any]] = []

    sandbox_op = config.sandbox_op
    probe_command = input_data.probe_command
    state_id = input_data.state_id

    # 合并蜜罐配置
    honeypot_vars = {**_DEFAULT_HONEYPOT_VARS, **config.honeypot_vars}
    forbidden_patterns = _DEFAULT_FORBIDDEN_PATTERNS + config.forbidden_network_patterns

    try:
        # Phase 1: 注入蜜罐凭证到沙箱环境
        trail.record(event="honeypot_inject", var_count=len(honeypot_vars))
        sandbox_env = os.environ.copy()
        sandbox_env.update(honeypot_vars)

        decision_trail.append({
            "step": "honeypot_injected",
            "honeypot_var_count": len(honeypot_vars),
            "honeypot_keys": list(honeypot_vars.keys()),
        })

        # Phase 2: 执行探针命令（带蜜罐环境）
        trail.record(event="sandbox_probe_honeypot", command=probe_command[:80])
        if sandbox_op is not None:
            try:
                probe_result = sandbox_op(
                    probe_command,
                    sandbox_op=None,
                    timeout=config.max_probe_timeout,
                    env=sandbox_env,
                )
            except Exception:
                probe_result = _fallback_probe(probe_command, sandbox_env, config.max_probe_timeout)
        else:
            probe_result = _fallback_probe(probe_command, sandbox_env, config.max_probe_timeout)

        stdout = probe_result.get("stdout", "") if isinstance(probe_result, dict) else ""
        stderr = probe_result.get("stderr", "") if isinstance(probe_result, dict) else ""
        combined_output = f"{stdout}\n{stderr}"

        decision_trail.append({
            "step": "sandbox_probe_completed",
            "exit_code": probe_result.get("exit_code") if isinstance(probe_result, dict) else -1,
        })

        # Phase 3: 蜜罐访问检测
        trail.record(event="honeypot_detection")
        honeypot_hits: list[str] = []
        for key, value in honeypot_vars.items():
            if value in combined_output:
                honeypot_hits.append(key)
            if key in combined_output:
                honeypot_hits.append(key)

        # Phase 4: 异常网络请求检测
        network_hits: list[dict[str, str]] = []
        for pattern in forbidden_patterns:
            matches = re.findall(pattern, combined_output, re.IGNORECASE)
            for match in matches:
                network_hits.append({
                    "pattern": pattern,
                    "match": str(match)[:100],
                })

        decision_trail.append({
            "step": "detection_complete",
            "honeypot_hits": len(honeypot_hits),
            "network_hits": len(network_hits),
        })

        is_escalated = len(honeypot_hits) > 0 or len(network_hits) > 0

        # Phase 5: 生成报告
        if is_escalated:
            severity = "CRITICAL" if len(honeypot_hits) > 0 else "HIGH"
            report_content = (
                f"# ⚠️ HONEYPOT ESCALATION REPORT\n\n"
                f"## Severity: {severity}\n"
                f"## State ID: {state_id}\n\n"
                f"### Honeypot Credential Hits ({len(honeypot_hits)}):\n"
                + "\n".join(f"- `{h}`" for h in honeypot_hits)
                + f"\n\n### Suspicious Network Patterns ({len(network_hits)}):\n"
                + "\n".join(f"- Pattern `{n['pattern']}` matched: `{n['match']}`" for n in network_hits)
                + f"\n\n### Recommendation\n"
                f"IMMEDIATE ACTION REQUIRED: Terminate sandbox, revoke all credentials, "
                f"initiate security incident response.\n"
            )
            final_status = "escalated"
        else:
            report_content = (
                f"# Honeypot Observation Report\n\n"
                f"## State ID: {state_id}\n"
                f"## Result: CLEAN — No honeypot access detected.\n"
                f"## Probe Output (truncated):\n```\n{combined_output[:500]}\n```\n"
            )
            final_status = "completed"

        report_path = write_report(
            report_content, output_dir=output_dir,
            name=f"honeypot_{state_id}",
            fmt="markdown",
        )

        cost.add(in_tok=300, out_tok=200)
        trail.write(output_dir)

        return build_result(
            status=final_status,
            error=None,
            cost_usd=cost.total_usd,
            report_path=str(report_path),
            trail=trail,
            trail_path=trail.write(output_dir),
            summary=f"Honeypot observation: escalated={is_escalated}",
            pillars={
                "fingerprint": {"state_id": state_id, "escalated": is_escalated},
                "decision_trail": decision_trail,
                "report": {
                    "escalated": is_escalated,
                    "honeypot_hits": honeypot_hits,
                    "network_hits": network_hits,
                    "severity": "CRITICAL" if honeypot_hits else "HIGH" if network_hits else "NONE",
                },
            },
            escalated=is_escalated,
            honeypot_hits=honeypot_hits,
            network_hits=network_hits,
        )

    except Exception as e:
        return build_result(
            status="failed",
            error={"type": type(e).__name__, "message": str(e)},
            cost_usd=cost.total_usd,
            trail=trail,
            summary=f"Honeypot observe failed: {e}",
            pillars={"decision_trail": decision_trail, "report": {"error": str(e)}},
        )


def _fallback_probe(
    command: str, env: dict[str, str], timeout: int
) -> dict[str, Any]:
    """回退探针执行（无 sandbox_op 注入时）."""
    import subprocess
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return {
            "status": "success",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "stdout": "", "stderr": "Timeout", "exit_code": -1}
    except Exception as e:
        return {"status": "error", "stdout": "", "stderr": str(e), "exit_code": -1}
