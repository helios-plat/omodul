from typing import Any
from .appstore_deploy import appstore_deploy, compute_fingerprint_for as compute_fingerprint_for_appstore_deploy
from .cross_project_health_aggregate import cross_project_health_aggregate, compute_fingerprint_for as compute_fingerprint_for_cross_project_health_aggregate
from .install_self_hosted_app import install_self_hosted_app, compute_fingerprint_for_install_self_hosted_app
from .upgrade_self_hosted_app import upgrade_self_hosted_app, compute_fingerprint_for_upgrade_self_hosted_app
from .backup_app_data import backup_app_data, compute_fingerprint_for_backup_app_data
from .configure_domain_for_app import configure_domain_for_app, compute_fingerprint_for_configure_domain_for_app
from .triage_signal import triage_signal, compute_fingerprint_for_triage_signal
from .diagnose_root_cause import diagnose_root_cause, compute_fingerprint_for_diagnose_root_cause
from .propose_action_plan import propose_action_plan, compute_fingerprint_for_propose_action_plan
from .generate_incident_postmortem import generate_incident_postmortem, compute_fingerprint_for_generate_incident_postmortem

# 统一的 compute_fingerprint_for(omodul_name, config, input_data) 路由
def compute_fingerprint_for(omodul_name: str, config: Any, input_data: Any) -> str:
    """服务层去重用. 按 omodul 名路由到具体 omodul 的 compute_fingerprint_for."""
    routers = {
        "appstore_deploy": compute_fingerprint_for_appstore_deploy,
        "cross_project_health_aggregate": compute_fingerprint_for_cross_project_health_aggregate,
        "install_self_hosted_app": compute_fingerprint_for_install_self_hosted_app,
        "upgrade_self_hosted_app": compute_fingerprint_for_upgrade_self_hosted_app,
        "backup_app_data": compute_fingerprint_for_backup_app_data,
        "configure_domain_for_app": compute_fingerprint_for_configure_domain_for_app,
        "triage_signal": compute_fingerprint_for_triage_signal,
        "diagnose_root_cause": compute_fingerprint_for_diagnose_root_cause,
        "propose_action_plan": compute_fingerprint_for_propose_action_plan,
        "generate_incident_postmortem": compute_fingerprint_for_generate_incident_postmortem,
    }
    
    if omodul_name not in routers:
        raise ValueError(f"Unknown omodul: {omodul_name}")
        
    return routers[omodul_name](config, input_data)

__all__ = [
    "appstore_deploy",
    "cross_project_health_aggregate",
    "install_self_hosted_app",
    "upgrade_self_hosted_app",
    "backup_app_data",
    "configure_domain_for_app",
    "triage_signal",
    "diagnose_root_cause",
    "propose_action_plan",
    "generate_incident_postmortem",
    "compute_fingerprint_for",
]
