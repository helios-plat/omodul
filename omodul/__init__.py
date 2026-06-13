from typing import Any

from ._base_config import BaseConfig
from .append_episode import AppendEpisodeConfig, append_episode
from .appstore_deploy import (
    appstore_deploy,
    compute_fingerprint_for as compute_fingerprint_for_appstore_deploy,
)
from .cross_project_health_aggregate import (
    cross_project_health_aggregate,
    compute_fingerprint_for as compute_fingerprint_for_cross_project_health_aggregate,
)
from .weekly_review_workflow import (
    weekly_review_workflow,
    compute_fingerprint_for as compute_fingerprint_for_weekly_review_workflow,
    WeeklyReviewConfig,
    WeeklyReviewInput,
    WeeklyReviewFindings,
    ActivityItem,
    ActivityGroup,
)
from .install_self_hosted_app import (
    install_self_hosted_app,
    compute_fingerprint_for_install_self_hosted_app,
)
from .upgrade_self_hosted_app import (
    upgrade_self_hosted_app,
    compute_fingerprint_for_upgrade_self_hosted_app,
)
from .backup_app_data import backup_app_data, compute_fingerprint_for_backup_app_data
from .configure_domain_for_app import (
    configure_domain_for_app,
    compute_fingerprint_for_configure_domain_for_app,
)
from .triage_signal import triage_signal, compute_fingerprint_for_triage_signal
from .diagnose_root_cause import diagnose_root_cause, compute_fingerprint_for_diagnose_root_cause
from .propose_action_plan import propose_action_plan, compute_fingerprint_for_propose_action_plan
from .generate_incident_postmortem import (
    generate_incident_postmortem,
    compute_fingerprint_for_generate_incident_postmortem,
)
from .register_entity import (
    RegisterEntityConfig,
    compute_fingerprint_for as compute_fingerprint_for_register_entity,
    register_entity,
)
from .knowledge_reflux import KnowledgeRefluxConfig, run_reflux
from .cognitive_diagnosis import CognitiveDiagnosisConfig, run_diagnosis

# --- AII 3O Batch 4b (v1.17.0) ---
from .register_ku import (
    RegisterKuConfig,
    compute_fingerprint_for as compute_fingerprint_for_register_ku,
    register_ku,
)
from .store_memory import StoreMemoryConfig, store_memory
from .reuse_strategy import ReuseStrategyConfig, reuse_strategy

# --- AII 3O Batch 5a — P4 GraphRAG (omodul 1.18.0) ---
from .graphrag_query import GraphRAGQueryConfig, graphrag_query

# --- AII 3O Batch 5b — P5 verification + learning + governance (omodul 1.19.0) ---
from .verify_knowledge import VerifyKnowledgeConfig, verify_knowledge
from .learning_distill import LearningDistillConfig, learning_distill
from .governance_adjudicate import GovernanceAdjudicateConfig, governance_adjudicate


# 统一的 compute_fingerprint_for(omodul_name, config, input_data) 路由
def compute_fingerprint_for(omodul_name: str, config: Any, input_data: Any) -> str:
    """服务层去重用. 按 omodul 名路由到具体 omodul 的 compute_fingerprint_for."""
    routers = {
        "appstore_deploy": compute_fingerprint_for_appstore_deploy,
        "cross_project_health_aggregate": compute_fingerprint_for_cross_project_health_aggregate,
        "weekly_review_workflow": compute_fingerprint_for_weekly_review_workflow,
        "install_self_hosted_app": compute_fingerprint_for_install_self_hosted_app,
        "upgrade_self_hosted_app": compute_fingerprint_for_upgrade_self_hosted_app,
        "backup_app_data": compute_fingerprint_for_backup_app_data,
        "configure_domain_for_app": compute_fingerprint_for_configure_domain_for_app,
        "triage_signal": compute_fingerprint_for_triage_signal,
        "diagnose_root_cause": compute_fingerprint_for_diagnose_root_cause,
        "propose_action_plan": compute_fingerprint_for_propose_action_plan,
        "generate_incident_postmortem": compute_fingerprint_for_generate_incident_postmortem,
        "symbol_dim_score": compute_fingerprint_for_symbol_dim_score,
        "regime_inference": compute_fingerprint_for_regime_inference,
        "candidate_pool": compute_fingerprint_for_candidate_pool,
        "macro_daily_report": compute_fingerprint_for_macro_daily_report_b11,
        "lhb_institution_vs_hotmoney_panel": compute_fingerprint_for_lhb_panel,
        "plan_card_render": compute_fingerprint_for_plan_card,
        "discipline_banner_toast_data": compute_fingerprint_for_discipline_banner,
        "monthly_review_cron_orchestrator": compute_fingerprint_for_monthly_review,
        "send_welcome_email": compute_fingerprint_for_send_welcome_email,
        "verify_email_workflow": compute_fingerprint_for_verify_email,
        "process_inbox_substrate": compute_fingerprint_for_process_inbox,
        "daily_digest_workflow": compute_fingerprint_for_daily_digest,
        "notification_dispatch_workflow": compute_fingerprint_for_notification_dispatch,
        "export_user_data_csv": compute_fingerprint_for_export_user_data,
        "sync_user_preferences": compute_fingerprint_for_sync_prefs,
        "register_entity": compute_fingerprint_for_register_entity,
        "register_ku": compute_fingerprint_for_register_ku,
        # Aegis 3O Batch 4 (v1.21.0)
        "diagnose_queue_health": compute_fingerprint_for_diagnose_queue_health,
        "diagnose_connection_pool": compute_fingerprint_for_diagnose_connection_pool,
        "diagnose_service_health": compute_fingerprint_for_diagnose_service_health,
    }

    if omodul_name not in routers:
        raise ValueError(f"Unknown omodul: {omodul_name}")

    return routers[omodul_name](config, input_data)


__all__ = [
    "BaseConfig",
    "appstore_deploy",
    "cross_project_health_aggregate",
    "weekly_review_workflow",
    "WeeklyReviewConfig",
    "WeeklyReviewInput",
    "WeeklyReviewFindings",
    "ActivityItem",
    "ActivityGroup",
    "install_self_hosted_app",
    "upgrade_self_hosted_app",
    "backup_app_data",
    "configure_domain_for_app",
    "triage_signal",
    "diagnose_root_cause",
    "propose_action_plan",
    "generate_incident_postmortem",
    "register_entity",
    "RegisterEntityConfig",
    "append_episode",
    "AppendEpisodeConfig",
    "run_reflux",
    "KnowledgeRefluxConfig",
    "run_diagnosis",
    "CognitiveDiagnosisConfig",
    "compute_fingerprint_for",
    # AII 3O Batch 4b (v1.17.0)
    "register_ku",
    "RegisterKuConfig",
    "store_memory",
    "StoreMemoryConfig",
    "reuse_strategy",
    "ReuseStrategyConfig",
    # AII 3O Batch 5a — P4 GraphRAG (omodul 1.18.0)
    "graphrag_query",
    "GraphRAGQueryConfig",
    # AII 3O Batch 5b — P5 verification + learning + governance (omodul 1.19.0)
    "verify_knowledge",
    "VerifyKnowledgeConfig",
    "learning_distill",
    "LearningDistillConfig",
    "governance_adjudicate",
    "GovernanceAdjudicateConfig",
    # v1.13.1
    "monthly_trade_review",
    "paper_trading_session",
    "individual_profile_workflow",
    "buy_sell_analysis",
    "daily_plan_generate",
    # v1.13.2
    "alert_calibration_engine",
    "training_task_recommend",
    "user_system_backtest",
    "panel_data_quality_check",
    # Stratum B3 (v1.14.0)
    "send_welcome_email",
    "WelcomeEmailConfig",
    "WelcomeEmailInput",
    "WelcomeEmailFindings",
    "reset_password_workflow",
    "ResetPasswordConfig",
    "ResetPasswordInput",
    "ResetPasswordFindings",
    "verify_email_workflow",
    "VerifyEmailConfig",
    "VerifyEmailInput",
    "VerifyEmailFindings",
    "process_inbox_substrate",
    "InboxConfig",
    "InboxInput",
    "InboxFindings",
    "daily_digest_workflow",
    "DailyDigestConfig",
    "DailyDigestInput",
    "DailyDigestFindings",
    "notification_dispatch_workflow",
    "NotifDispatchConfig",
    "NotifDispatchInput",
    "NotifDispatchFindings",
    "export_user_data_csv",
    "ExportUserDataConfig",
    "ExportUserDataInput",
    "ExportUserDataFindings",
    "sync_user_preferences",
    "SyncPrefsConfig",
    "SyncPrefsInput",
    "SyncPrefsFindings",
    # Aegis 3O Batch 4 (v1.21.0)
    "DiagnoseQueueHealthConfig",
    "DiagnoseQueueHealthInput",
    "DiagnoseQueueHealthFindings",
    "QueueStat",
    "diagnose_queue_health",
    "DiagnoseConnectionPoolConfig",
    "DiagnoseConnectionPoolInput",
    "DiagnoseConnectionPoolFindings",
    "SlowQuerySummary",
    "diagnose_connection_pool",
    "DiagnoseServiceHealthConfig",
    "DiagnoseServiceHealthInput",
    "DiagnoseServiceHealthFindings",
    "diagnose_service_health",
]

# Sprint 13 — C1 + C2
from .policy_sector_classify_workflow import (
    policy_sector_classify_workflow,
    compute_fingerprint_for as compute_fingerprint_for_policy_sector_classify,
    PolicySectorClassifyConfig,
)
from .macro_daily_report_workflow import (
    macro_daily_report_workflow,
    compute_fingerprint_for as compute_fingerprint_for_macro_daily_report,
    MacroDailyReportConfig,
)

# P6-B6
from .audience_data_workflow import (
    audience_data_workflow,
    compute_fingerprint_for as compute_fingerprint_for_audience_data,
    AudienceDataConfig,
    AudienceDataInput,
    AudienceDataFindings,
)

# --- Helios Wave 01: Crypto omoduls (3) ---
from .fusion_score_workflow import (
    fusion_score_workflow,
    compute_fingerprint_for as compute_fingerprint_for_fusion_score,
    FusionScoreConfig,
)
from .market_summary_workflow import (
    market_summary_workflow,
    compute_fingerprint_for as compute_fingerprint_for_market_summary,
    MarketSummaryConfig,
)
from .timeframes_compute_workflow import (
    timeframes_compute_workflow,
    compute_fingerprint_for as compute_fingerprint_for_timeframes,
    TimeframesConfig,
)

# --- Tide v4 extraction: B3-B5 (3 omoduls) ---
from omodul.symbol_dim_score import (
    symbol_dim_score,
    SymbolDimScoreConfig,
    SymbolDimScoreInput,
    SymbolDimScoreFindings,
    compute_fingerprint_for as compute_fingerprint_for_symbol_dim_score,
)
from omodul.regime_inference import (
    regime_inference,
    RegimeInferenceConfig,
    RegimeInferenceInput,
    compute_fingerprint_for as compute_fingerprint_for_regime_inference,
)
from omodul.candidate_pool import (
    candidate_pool,
    CandidatePoolConfig,
    CandidatePoolInput,
    compute_fingerprint_for as compute_fingerprint_for_candidate_pool,
)

# --- Tide v4 step2: B11 (5 omoduls) ---
from omodul.macro_daily_report import (
    macro_daily_report,
    MacroDailyReportConfig as MacroDailyReportConfigB11,
    MacroDailyReportInput,
    MacroDailyReportFindings,
    compute_fingerprint_for as compute_fingerprint_for_macro_daily_report_b11,
)
from omodul.lhb_institution_vs_hotmoney_panel import (
    lhb_institution_vs_hotmoney_panel,
    LhbPanelConfig,
    LhbPanelInput,
    LhbPanelFindings,
    compute_fingerprint_for as compute_fingerprint_for_lhb_panel,
)
from omodul.plan_card_render import (
    plan_card_render,
    PlanCardConfig,
    PlanCardInput,
    PlanCardFindings,
    compute_fingerprint_for as compute_fingerprint_for_plan_card,
)
from omodul.discipline_banner_toast_data import (
    discipline_banner_toast_data,
    DisciplineBannerConfig,
    DisciplineBannerInput,
    DisciplineBannerFindings,
    compute_fingerprint_for as compute_fingerprint_for_discipline_banner,
)
from omodul.monthly_review_cron_orchestrator import (
    monthly_review_cron_orchestrator,
    MonthlyReviewConfig,
    MonthlyReviewInput,
    MonthlyReviewFindings,
    compute_fingerprint_for as compute_fingerprint_for_monthly_review,
)
from omodul.behavior import monthly_trade_review, training_task_recommend
from omodul.simulation.paper_trading_session import paper_trading_session
from omodul.profile.individual_profile_workflow import individual_profile_workflow
from omodul.signals import alert_calibration_engine, buy_sell_analysis
from omodul.strategy.daily_plan_generator import daily_plan_generate
from omodul.backtest.user_system_backtest import user_system_backtest
from omodul.data_quality import panel_data_quality_check

# --- Stratum B3 — 8 omodul (v1.14.0) ---
from omodul.send_welcome_email import (
    WelcomeEmailConfig,
    WelcomeEmailFindings,
    WelcomeEmailInput,
    compute_fingerprint_for as compute_fingerprint_for_send_welcome_email,
    send_welcome_email,
)
from omodul.reset_password_workflow import (
    ResetPasswordConfig,
    ResetPasswordFindings,
    ResetPasswordInput,
    reset_password_workflow,
)
from omodul.verify_email_workflow import (
    VerifyEmailConfig,
    VerifyEmailFindings,
    VerifyEmailInput,
    compute_fingerprint_for as compute_fingerprint_for_verify_email,
    verify_email_workflow,
)
from omodul.process_inbox_substrate import (
    InboxConfig,
    InboxFindings,
    InboxInput,
    compute_fingerprint_for as compute_fingerprint_for_process_inbox,
    process_inbox_substrate,
)
from omodul.daily_digest_workflow import (
    DailyDigestConfig,
    DailyDigestFindings,
    DailyDigestInput,
    compute_fingerprint_for as compute_fingerprint_for_daily_digest,
    daily_digest_workflow,
)
from omodul.notification_dispatch_workflow import (
    NotifDispatchConfig,
    NotifDispatchFindings,
    NotifDispatchInput,
    compute_fingerprint_for as compute_fingerprint_for_notification_dispatch,
    notification_dispatch_workflow,
)
from omodul.export_user_data_csv import (
    ExportUserDataConfig,
    ExportUserDataFindings,
    ExportUserDataInput,
    compute_fingerprint_for as compute_fingerprint_for_export_user_data,
    export_user_data_csv,
)
from omodul.sync_user_preferences import (
    SyncPrefsConfig,
    SyncPrefsFindings,
    SyncPrefsInput,
    compute_fingerprint_for as compute_fingerprint_for_sync_prefs,
    sync_user_preferences,
)

# v1.15.0 — builtin agents re-export (避免 export 漏症再发)
from omodul.knowledge.agents.builtin import IllustrationAgent

# --- Aegis 3O Batch 4 (v1.21.0) ---
from omodul.diagnose_queue_health import (
    DiagnoseQueueHealthConfig,
    DiagnoseQueueHealthInput,
    DiagnoseQueueHealthFindings,
    QueueStat,
    diagnose_queue_health,
    compute_fingerprint_for_diagnose_queue_health,
)
from omodul.diagnose_connection_pool import (
    DiagnoseConnectionPoolConfig,
    DiagnoseConnectionPoolInput,
    DiagnoseConnectionPoolFindings,
    SlowQuerySummary,
    diagnose_connection_pool,
    compute_fingerprint_for_diagnose_connection_pool,
)
from omodul.diagnose_service_health import (
    DiagnoseServiceHealthConfig,
    DiagnoseServiceHealthInput,
    DiagnoseServiceHealthFindings,
    diagnose_service_health,
    compute_fingerprint_for_diagnose_service_health,
)

# --- hevi v2 (v1.26.0) ---
from omodul.agentic_longvideo_pipeline import (
    LongVideoConfig,
    LongVideoResult,
    agentic_longvideo_pipeline,
)
