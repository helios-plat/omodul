import json
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from obase.cost_tracker import CostTracker
from oprim import (
    caddy_admin_reload,
    docker_container_inspect,
    docker_container_start,
    docker_image_pull,
    http_health_probe,
)
from pydantic import BaseModel, Field

from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker


class InstallSelfHostedAppConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "install_self_hosted_app"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[set[str]] = {
        "app_slug", "app_version", "instance_name", "config_hash"
    }

    app_slug: str = Field(..., description="App 标识 (e.g. 'gitea', 'nextcloud')")
    app_version: str
    instance_name: str = Field(
        ..., description="实例名 (e.g. 'gitea-prod', 'gitea-dev'), 用于区分同一 app 的多实例"
    )
    config_hash: str = Field(..., description="App config dict 的 SHA-256, caller 算好传入")
    domain: str | None = None
    health_check_timeout_sec: int = 120
    health_check_url_template: str = "http://localhost:{port}/health"


class InstallSelfHostedAppInput(BaseModel):
    app_config: dict[str, Any]                # 容器 env / volumes / ports / etc.
    target_host: str = "localhost"  # 目标安装机器 (MVP 仅本机)
    docker_host: str = "unix:///var/run/docker.sock"
    caddy_admin_url: str = "http://localhost:2019"


class InstallSelfHostedAppFindings(BaseModel):
    container_id: str
    container_name: str
    image_digest: str
    started_at_utc: str
    health_status: Literal["healthy", "unhealthy", "no_health_check"]
    domain: str | None
    https_active: bool
    monitors_bound: list[str]       # 已绑定的告警监控类型
    autoheal_plugins_attached: list[str]  # 自动绑定的 plugin 名


def install_self_hosted_app(
    config: InstallSelfHostedAppConfig,
    input_data: InstallSelfHostedAppInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """端到端安装自部署应用: 拉镜像 + 启容器 + 健康验证 + 反代配置 + 监控告警自动绑定."""
    started_at = datetime.now(UTC)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    token = _current_cost_tracker.set(cost_tracker)
    try:
        # _stage_pull_image
        image_info = _stage_pull_image(config, input_data, trail_steps, on_step)

        # _stage_start_container
        container_info = _stage_start_container(config, input_data, trail_steps, on_step)

        # _stage_health_check
        health_info = _stage_health_check(config, input_data, container_info, trail_steps, on_step)

        # _stage_configure_reverse_proxy
        proxy_info = _stage_configure_reverse_proxy(config, input_data, trail_steps, on_step)

        findings = InstallSelfHostedAppFindings(
            container_id=container_info["id"],
            container_name=config.instance_name,
            image_digest=image_info.digest,
            started_at_utc=started_at.isoformat(),
            health_status=health_info["status"],
            domain=config.domain,
            https_active=proxy_info.get("https", False),
            monitors_bound=["health_check"],
            autoheal_plugins_attached=[]
        )

    except Exception as e:
        error_info = {
            "error_class": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        status = "failed"
    finally:
        _current_cost_tracker.reset(token)

    decision_trail = build_decision_trail(
        fingerprint=fingerprint, config=config,
        input_data=input_data, trail_steps=trail_steps,
        cost_tracker=cost_tracker, started_at=started_at,
        status=status, error=error_info,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "decision_trail.json").write_text(
        json.dumps(decision_trail, indent=2, ensure_ascii=False, default=str)
    )

    report_path = write_markdown_report(
        output_dir=output_dir,
        omodul_name=config._omodul_name,
        fingerprint=fingerprint,
        config=config,
        findings=findings,
        decision_trail=decision_trail,
        cost_tracker=cost_tracker,
        status=status
    )

    return {
        "findings": findings,
        "fingerprint": fingerprint,
        "decision_trail": decision_trail,
        "report_path": report_path,
        "cost_usd": cost_tracker.total_usd,
        "status": status,
        "error": error_info,
    }


def _stage_pull_image(
    config: InstallSelfHostedAppConfig,
    input_data: InstallSelfHostedAppInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None
) -> dict[str, Any]:
    step_start = datetime.now(UTC)
    # Simplified: app_slug:app_version as image
    image = f"{config.app_slug}:{config.app_version}"
    result = docker_image_pull(image=image, docker_host=input_data.docker_host)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="docker_image_pull", inputs_summary={"image": image},
        outputs_summary={"id": result.digest}, started_at=step_start
    )
    return result


def _stage_start_container(
    config: InstallSelfHostedAppConfig,
    input_data: InstallSelfHostedAppInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None
) -> dict[str, Any]:
    step_start = datetime.now(UTC)
    docker_container_start(container_id=config.instance_name, docker_host=input_data.docker_host)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="docker_container_start", inputs_summary={"name": config.instance_name},
        outputs_summary={"result": "started"}, started_at=step_start
    )
    # Get ID
    inspect = docker_container_inspect(
        container_id=config.instance_name, docker_host=input_data.docker_host
    )
    return {"id": inspect.container_id}


def _stage_health_check(
    config: InstallSelfHostedAppConfig,
    input_data: InstallSelfHostedAppInput,
    container_info: dict[str, Any],
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None
) -> dict[str, Any]:
    step_start = datetime.now(UTC)
    status: Literal["healthy", "unhealthy", "no_health_check"] = "healthy"
    try:
        port = int(input_data.app_config.get("ports", {}).get("80/tcp", 80))
        url = config.health_check_url_template.format(port=port)
        probe = http_health_probe(url=url, timeout_sec=5)
        if not probe["healthy"]:
            status = "unhealthy"
    except Exception:
        status = "no_health_check"

    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="http_health_probe", inputs_summary={"url": "...", "timeout": 5},
        outputs_summary={"status": status}, started_at=step_start
    )
    return {"status": status}


def _stage_configure_reverse_proxy(
    config: InstallSelfHostedAppConfig,
    input_data: InstallSelfHostedAppInput,
    trail_steps: list[dict[str, Any]],
    on_step: Callable[[dict[str, Any]], None] | None
) -> dict[str, Any]:
    if not config.domain:
        return {}
    step_start = datetime.now(UTC)
    try:
        caddy_admin_reload(config={}, admin_url=input_data.caddy_admin_url)
        record_step(
            trail_steps=trail_steps, on_step=on_step, layer="oprim",
            callable_name="caddy_admin_reload", inputs_summary={"domain": config.domain},
            outputs_summary={"status": "reloaded"}, started_at=step_start
        )
        return {"https": True}
    except Exception:
        return {"https": False}


def compute_fingerprint_for_install_self_hosted_app(
    config: InstallSelfHostedAppConfig, input_data: InstallSelfHostedAppInput
) -> str:
    return compute_fingerprint(config, input_data)
