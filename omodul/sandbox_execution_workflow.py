"""omodul.sandbox_execution_workflow — 受控沙箱业务执行事务.

集成：命令执行（oprim.sandbox_exec）+ 日志收集 + 超时熔断 + 资源审计，
产出 decision_trail 与执行报告。

Pillars: decision_trail, report, cost
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from omodul._base import BaseConfig, CostTracker, Trail, build_result, write_report


class SandboxExecutionConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "sandbox_execution_workflow"
    _omodul_version: ClassVar[str] = "1.0.0"
    _enabled_pillars: ClassVar[set[str]] = {"decision_trail", "report", "cost"}

    timeout: float = 60.0
    max_stdout_chars: int = 20_000
    report_format: str = "markdown"


class SandboxExecutionInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    command: str
    container_id: str | None = None  # 缺省时用 config.pool 或 input.docker 获取
    workdir: str | None = None
    env: dict[str, str] | None = None
    docker: Any = None  # obase.docker_pool.DockerPool 实例（container_id 缺省时使用）
    on_timeout: str = "kill"  # "kill" | "fail"


async def sandbox_execution_workflow(
    config: SandboxExecutionConfig,
    input_data: SandboxExecutionInput,
    output_dir: Path,
    *,
    on_step=None,
) -> dict:
    """在沙箱容器内执行命令，收集日志并输出审计报告。

    失败处理：任何异常捕获后返回 {"status": "failed", "error": {...}}，
    绝不 raise。
    """
    trail = Trail()
    cost = CostTracker()
    t0 = time.time()

    try:
        from oprim import sandbox_exec

        container_id = input_data.container_id
        if container_id is None:
            if input_data.docker is None:
                raise ValueError(
                    "container_id is required, or inject a DockerPool via input_data.docker"
                )
            container_id = await input_data.docker.acquire(name_hint="sandbox")
            trail.record(event="container_acquired", container_id=container_id)

        trail.record(
            event="sandbox_exec_start",
            command=input_data.command[:500],
            container_id=container_id,
            timeout=config.timeout,
        )
        if on_step:
            on_step({"stage": "exec", "status": "running", "command": input_data.command[:100]})

        result = await sandbox_exec(
            input_data.command,
            container_id=container_id,
            timeout=config.timeout,
            workdir=input_data.workdir,
            env=input_data.env,
        )

        stdout = result.get("stdout", "")[: config.max_stdout_chars]
        stderr = result.get("stderr", "")[: config.max_stdout_chars]
        exit_code = result.get("exit_code")
        timed_out = result.get("status") == "timeout"

        trail.record(
            event="sandbox_exec_done",
            exit_code=exit_code,
            timed_out=timed_out,
            stdout_chars=len(stdout),
            stderr_chars=len(stderr),
            elapsed_ms=result.get("elapsed_ms"),
        )

        # 资源审计
        audit = {
            "container_id": container_id,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout_chars": len(stdout),
            "stderr_chars": len(stderr),
            "elapsed_ms": result.get("elapsed_ms"),
            "wall_s": round(time.time() - t0, 3),
        }

        if timed_out and input_data.on_timeout == "fail":
            raise TimeoutError(f"sandbox command timed out: {input_data.command[:100]}")

        report = _render_report(input_data.command, audit, stdout, stderr)
        report_path = write_report(
            report, output_dir=Path(output_dir), name="sandbox_execution_report",
            fmt=config.report_format,
        )

        if on_step:
            on_step({"stage": "done", "status": "completed", "exit_code": exit_code})

        return build_result(
            status="completed",
            error=None,
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            report_path=report_path,
            cost_usd=cost.total_usd,
            audit=audit,
            stdout=stdout,
            stderr=stderr,
        )

    except Exception as exc:
        trail.record(event="error", detail=f"{type(exc).__name__}: {exc}")
        return build_result(
            status="failed",
            error={"type": type(exc).__name__, "message": str(exc)},
            trail=trail,
            trail_path=trail.write(Path(output_dir)) if output_dir else None,
            cost_usd=cost.total_usd,
        )


def _render_report(command: str, audit: dict, stdout: str, stderr: str) -> str:
    lines = [
        "# 沙箱执行报告",
        "",
        f"- 命令: `{command}`",
        f"- 容器: `{audit['container_id']}`",
        f"- 退出码: {audit['exit_code']}",
        f"- 超时: {audit['timed_out']}",
        f"- 耗时: {audit['elapsed_ms']}ms / wall {audit['wall_s']}s",
        "",
        "## stdout",
        "```",
        stdout,
        "```",
        "",
        "## stderr",
        "```",
        stderr,
        "```",
    ]
    return "\n".join(lines)
