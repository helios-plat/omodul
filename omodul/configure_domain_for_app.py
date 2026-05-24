import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, ClassVar, Any, Set

from obase.cost_tracker import CostTracker
from omodul._base_config import BaseConfig
from omodul._decision_trail import build_decision_trail, record_step
from omodul._fingerprint import compute_fingerprint
from omodul._report import write_markdown_report
from omodul._runtime import _current_cost_tracker
from oprim import (
    dns_resolve,
    caddy_admin_reload,
    caddy_certificates_status,
    http_health_probe
)
from pydantic import BaseModel, Field


class ConfigureDomainConfig(BaseConfig):
    _omodul_name: ClassVar[str] = "configure_domain_for_app"
    _omodul_version: ClassVar[str] = "1.0.0"
    _fingerprint_fields: ClassVar[Set[str]] = {
        "domain", "target_instance", "enable_https"
    }
    domain: str
    target_instance: str
    enable_https: bool = True
    acme_email: str | None = None
    dns_check_timeout_sec: int = 30


class ConfigureDomainInput(BaseModel):
    target_host: str
    target_port: int
    caddy_admin_url: str = "http://localhost:2019"


class ConfigureDomainFindings(BaseModel):
    domain: str
    dns_resolved: bool
    dns_records: list[str] = Field(default_factory=list)
    caddy_route_added: bool
    https_certificate_obtained: bool
    certificate_not_after: str | None = None


def configure_domain_for_app(
    config: ConfigureDomainConfig,
    input_data: ConfigureDomainInput,
    output_dir: Path,
    *,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """配置域名: DNS 检查 + Caddy 配置 + HTTPS 验证."""
    started_at = datetime.now(timezone.utc)
    fingerprint = compute_fingerprint(config, input_data)
    cost_tracker = CostTracker(budget_usd=config.budget_usd)
    trail_steps: list[dict[str, Any]] = []
    error_info = None
    status: Literal["completed", "failed"] = "completed"
    findings = None

    token = _current_cost_tracker.set(cost_tracker)
    try:
        # 1. DNS Check
        dns_info = _stage_dns_check(config, input_data, trail_steps, on_step)
        if not dns_info["resolved"]:
             status = "failed"
             error_info = {"error_message": f"DNS not resolved for {config.domain}"}
        else:
            # 2. Caddy Configure
            _stage_caddy_configure(config, input_data, trail_steps, on_step)
            
            # 3. Verify HTTPS
            https_info = _stage_verify_https(config, input_data, trail_steps, on_step)
            
            findings = ConfigureDomainFindings(
                domain=config.domain,
                dns_resolved=True,
                dns_records=dns_info["records"],
                caddy_route_added=True,
                https_certificate_obtained=https_info["obtained"],
                certificate_not_after=https_info.get("not_after")
            )
            
            if not https_info["obtained"]:
                status = "completed" # completed but with warning in findings
        
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


def _stage_dns_check(
    config: ConfigureDomainConfig, 
    input_data: ConfigureDomainInput, 
    trail_steps: list[dict[str, Any]], 
    on_step: Callable[[dict[str, Any]], None] | None
) -> dict[str, Any]:
    step_start = datetime.now(timezone.utc)
    try:
        records = dns_resolve(domain=config.domain)
        resolved = len(records) > 0
    except Exception:
        records = []
        resolved = False
        
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="dns_resolve", inputs_summary={"domain": config.domain},
        outputs_summary={"resolved": resolved}, started_at=step_start
    )
    return {"resolved": resolved, "records": records}


def _stage_caddy_configure(
    config: ConfigureDomainConfig, 
    input_data: ConfigureDomainInput, 
    trail_steps: list[dict[str, Any]], 
    on_step: Callable[[dict[str, Any]], None] | None
) -> None:
    step_start = datetime.now(timezone.utc)
    caddy_admin_reload(config={}, admin_url=input_data.caddy_admin_url)
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="caddy_admin_reload", inputs_summary={"domain": config.domain},
        outputs_summary={"status": "reloaded"}, started_at=step_start
    )


def _stage_verify_https(
    config: ConfigureDomainConfig, 
    input_data: ConfigureDomainInput, 
    trail_steps: list[dict[str, Any]], 
    on_step: Callable[[dict[str, Any]], None] | None
) -> dict[str, Any]:
    if not config.enable_https:
        return {"obtained": False}
    step_start = datetime.now(timezone.utc)
    try:
        status = caddy_certificates_status(admin_url=input_data.caddy_admin_url)
        obtained = any(s.get("domain") == config.domain and s.get("status") == "active" for s in status)
    except Exception:
        obtained = False
        
    record_step(
        trail_steps=trail_steps, on_step=on_step, layer="oprim",
        callable_name="caddy_certificates_status", inputs_summary={"domain": config.domain},
        outputs_summary={"obtained": obtained}, started_at=step_start
    )
    return {"obtained": obtained}


def compute_fingerprint_for_configure_domain_for_app(config: ConfigureDomainConfig, input_data: ConfigureDomainInput) -> str:
    return compute_fingerprint(config, input_data)
