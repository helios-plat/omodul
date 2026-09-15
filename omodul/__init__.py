__version__ = "1.49.0"
from typing import Any

from omodul.action_gateway import (
    ExecuteGovernedActionConfig,
    ExecuteGovernedActionInput,
    GovernActionConfig,
    GovernActionInput,
    execute_governed_action,
    govern_action,
)
from omodul.add_customer_address import add_customer_address
from omodul.add_customer_address import (
    compute_fingerprint_for as add_customer_address_fingerprint,
)
from omodul.add_line_item_to_cart import add_line_item_to_cart
from omodul.add_line_item_to_cart import (
    compute_fingerprint_for as add_line_item_to_cart_fingerprint,
)
from omodul.add_prices_to_list import add_prices_to_list
from omodul.add_shipping_method_to_cart import add_shipping_method_to_cart
from omodul.add_shipping_method_to_cart import (
    compute_fingerprint_for as add_shipping_method_to_cart_fingerprint,
)
from omodul.adjust_inventory_level import adjust_inventory_level

# ── 红蓝对抗审判庭 (高阶审判模块) ────────────────────────────────────
from omodul.adversarial_chamber import (
    AdversarialChamberConfig,
    AdversarialChamberInput,
    adversarial_chamber,
)
from omodul.adversarial_honeypot_observe import (
    DEFAULT_HONEYPOT_ENV,
    HoneypotObservation,
    HoneypotSandboxResult,
    adversarial_honeypot_observe,
)
from omodul.apply_discount_to_cart import apply_discount_to_cart
from omodul.apply_discount_to_cart import (
    compute_fingerprint_for as apply_discount_to_cart_fingerprint,
)
from omodul.apply_gift_card_to_cart import apply_gift_card_to_cart
from omodul.apply_gift_card_to_cart import (
    compute_fingerprint_for as apply_gift_card_to_cart_fingerprint,
)
from omodul.archive_order import archive_order

# G1 Artifact 预览 (pi-workbench 复刻)
from omodul.artifact_preview import (
    ARTIFACT_TYPES,
    artifact_preview,
    sanitize_markup,
)
from omodul.assign_customer_to_group import assign_customer_to_group
from omodul.assign_customer_to_group import (
    compute_fingerprint_for as assign_customer_to_group_fingerprint,
)
from omodul.authorize_payment_for_cart import authorize_payment_for_cart
from omodul.authorize_payment_for_cart import (
    compute_fingerprint_for as authorize_payment_for_cart_fingerprint,
)

# cindy_mcp_server available via lazy import
# ── Veya Agent OS 编排层 (P3 固化) ────────────────────────────────────────
from omodul.automata import AutomataScheduler
from omodul.browser_session import (
    PrepareBrowserSessionConfig,
    PrepareBrowserSessionInput,
    prepare_browser_session,
)
from omodul.cancel_batch_job import cancel_batch_job
from omodul.cancel_claim import cancel_claim
from omodul.cancel_fulfillment import cancel_fulfillment
from omodul.cancel_order import cancel_order
from omodul.cancel_return import cancel_return
from omodul.cancel_swap import cancel_swap
from omodul.candidate_pool import (
    CandidatePoolConfig,
    CandidatePoolInput,
    candidate_pool,
)
from omodul.candidate_pool import (
    compute_fingerprint_for as compute_fingerprint_for_candidate_pool,
)
from omodul.capture_payment import capture_payment

# ── Phase 2: 因果诊断 (O1) + 蜜罐反间谍 (O3) 事务 ─────────────────────
from omodul.causal_fault_diagnose import (
    CausalDiagnosisReport,
    NodeInterventionResult,
    causal_fault_diagnose,
)

# ── 连续 Cholesky SCM (多维遥测 L3 溯因) ────────────────────────────────
from omodul.cholesky_scm import ContinuousCholeskySCM, ContinuousNode

# ── Phase 3: 反脆弱闭环 (最优干预 / 在线因果更新 / 威胁演化) ────────────
from omodul.closed_loop_intervene import (
    ClosedLoopConfig,
    ClosedLoopInput,
    closed_loop_intervene,
)

# ── 代码 Agent 可靠性闭环 (方案 A+C) ─────────────────────────────────────
from omodul.code_reliability_loop import (
    CodeLoopResult,
    CodeTask,
    FailureKind,
    FailureSignature,
    PatchArtifact,
    TestResult,
    run_code_reliability_loop,
)
from omodul.compact_session import compact_session
from omodul.compact_session import compute_fingerprint_for as compact_session_fingerprint
from omodul.complete_checkout import complete_checkout
from omodul.complete_checkout import (
    compute_fingerprint_for as complete_checkout_fingerprint,
)
from omodul.computer_session import (
    PrepareComputerSessionConfig,
    PrepareComputerSessionInput,
    prepare_computer_session,
)

# ── AII Conflict Detection Workflow (M-G1) ───────────────────────────────────
from omodul.conflict_detection_workflow import ConflictDetectionConfig, conflict_detection_workflow

# ── Phase 4 延伸: L3 反事实诊断事务 (针对本次故障) ─────────────────────
from omodul.counterfactual_diagnose import (
    CounterfactualDiagnosisReport,
    CounterfactualReport,
    counterfactual_diagnose,
)
from omodul.create_batch_job import create_batch_job
from omodul.create_cart import compute_fingerprint_for as create_cart_fingerprint
from omodul.create_cart import create_cart
from omodul.create_claim import create_claim
from omodul.create_customer import compute_fingerprint_for as create_customer_fingerprint
from omodul.create_customer import create_customer
from omodul.create_customer_group import (
    compute_fingerprint_for as create_customer_group_fingerprint,
)
from omodul.create_customer_group import create_customer_group
from omodul.create_discount import compute_fingerprint_for as create_discount_fingerprint
from omodul.create_discount import create_discount
from omodul.create_discount_condition import (
    compute_fingerprint_for as create_discount_condition_fingerprint,
)
from omodul.create_discount_condition import create_discount_condition
from omodul.create_discount_rule import (
    compute_fingerprint_for as create_discount_rule_fingerprint,
)
from omodul.create_discount_rule import create_discount_rule
from omodul.create_draft_order import create_draft_order
from omodul.create_fulfillment import create_fulfillment
from omodul.create_gift_card import create_gift_card
from omodul.create_inventory_batch import (
    compute_fingerprint_for as create_inventory_batch_fingerprint,
)

# Batch-warehouse commerce vertical
from omodul.create_inventory_batch import create_inventory_batch
from omodul.create_payment_sessions import (
    compute_fingerprint_for as create_payment_sessions_fingerprint,
)
from omodul.create_payment_sessions import create_payment_sessions
from omodul.create_price_list import create_price_list
from omodul.create_product import create_product
from omodul.create_product_category import create_product_category
from omodul.create_product_collection import create_product_collection
from omodul.create_product_option import create_product_option
from omodul.create_product_variant import create_product_variant
from omodul.create_region import compute_fingerprint_for as create_region_fingerprint
from omodul.create_region import create_region
from omodul.create_return_request import create_return_request
from omodul.create_sales_channel import create_sales_channel
from omodul.create_session import compute_fingerprint_for as create_session_fingerprint
from omodul.create_session import create_session
from omodul.create_stock_location import create_stock_location
from omodul.create_swap import create_swap
from omodul.create_tax_rate import compute_fingerprint_for as create_tax_rate_fingerprint
from omodul.create_tax_rate import create_tax_rate
from omodul.create_user import create_user
from omodul.delete_customer_address import (
    compute_fingerprint_for as delete_customer_address_fingerprint,
)
from omodul.delete_customer_address import delete_customer_address
from omodul.delete_discount import compute_fingerprint_for as delete_discount_fingerprint
from omodul.delete_discount import delete_discount
from omodul.delete_discount_condition import (
    compute_fingerprint_for as delete_discount_condition_fingerprint,
)
from omodul.delete_discount_condition import delete_discount_condition
from omodul.delete_draft_order import delete_draft_order
from omodul.delete_gift_card import delete_gift_card
from omodul.delete_line_item_from_cart import (
    compute_fingerprint_for as delete_line_item_from_cart_fingerprint,
)
from omodul.delete_line_item_from_cart import delete_line_item_from_cart
from omodul.delete_price_list import delete_price_list
from omodul.delete_product import delete_product
from omodul.delete_product_category import delete_product_category
from omodul.delete_product_collection import delete_product_collection
from omodul.delete_product_option import delete_product_option
from omodul.delete_product_variant import delete_product_variant
from omodul.delete_region import compute_fingerprint_for as delete_region_fingerprint
from omodul.delete_region import delete_region
from omodul.delete_sales_channel import delete_sales_channel
from omodul.delete_stock_location import delete_stock_location
from omodul.delete_tax_rate import compute_fingerprint_for as delete_tax_rate_fingerprint
from omodul.delete_tax_rate import delete_tax_rate
from omodul.execute_tool import execute_tool
from omodul.export_substrate_markdown import (
    ExportSubstrateMarkdownConfig,
    ExportSubstrateMarkdownInput,
    export_substrate_markdown,
)
from omodul.force_analysis_workflow import (
    ForceAnalysisConfig,
    ForceAnalysisInput,
    force_analysis_workflow,
)
from omodul.fork_session import compute_fingerprint_for as fork_session_fingerprint
from omodul.fork_session import fork_session
from omodul.fulfill_claim import fulfill_claim
from omodul.fulfill_swap import fulfill_swap
from omodul.governed_mcp_transaction import (
    GovernedMcpConfig,
    GovernedMcpInput,
    governed_mcp_transaction,
)
from omodul.governed_tool_transaction import (
    GovernedToolConfig,
    GovernedToolInput,
    governed_tool_transaction,
)
from omodul.hitl_approval import ApprovalGate
from omodul.index_codebase import compute_fingerprint_for as index_codebase_fingerprint
from omodul.index_codebase import index_codebase
from omodul.init_project import init_project
from omodul.login_provider import login_provider

# 长程任务 GoalKernel 投影状态机 (事件溯源, 依赖 obase.loop_event_store)
from omodul.long_task_goal import (
    EVENT_EVIDENCE_APPENDED,
    EVENT_GATE_REQUIRED,
    EVENT_GATE_RESOLVED,
    EVENT_GOAL_ADDED,
    EVENT_HANDOFF_RECORDED,
    EVENT_TODO_UPDATED,
    Evidence,
    Gate,
    Goal,
    GoalKernel,
    GoalKernelError,
    Handoff,
    IntegrityResult,
    QuotaView,
    Todo,
)
from omodul.mark_draft_order_paid import mark_draft_order_paid
from omodul.mission_revert import (
    WorktreeState,
    mission_revert,
    snapshot_mission_baseline,
)

# G3/G4 Vigla 复刻: Merge 审计 + Mission 回滚
from omodul.mission_supervisor import (
    DEFAULT_SECRET_PATTERNS,
    SupervisorPolicy,
    mission_supervisor,
    parse_diff,
)
from omodul.model_router import ModelRouter

# ── Phase 4: 多步反事实规划事务 ───────────────────────────────────────
from omodul.multi_step_plan import (
    ExecutionResult,
    MultiStepPlanReport,
    multi_step_plan,
    update_cpd_from_repair,
)

# ── 神经符号 / 组合优化 / 沙箱推演 编排管线 (O1/O2/O3) ───────────────────
from omodul.neuro_symbolic import (
    NeuroSymbolicResult,
    RepairPayload,
    compute_plan_id,
    run_neuro_symbolic,
)
from omodul.observer import ObserverConfig, run_observer_lookahead
from omodul.operator_center import (
    OperatorDecision,
    OperatorEscalation,
    render_decision,
    run_operator_center,
)

# New omodul modules (batch 1.29)
from omodul.process_prompt import process_prompt
from omodul.process_swap_payment import process_swap_payment
from omodul.provider_inference import (
    ProviderInferenceConfig,
    ProviderInferenceInput,
    provider_inference_transaction,
)
from omodul.publish_products_to_channel import publish_products_to_channel
from omodul.reading_guide_workflow import (
    ReadingGuideConfig,
    ReadingGuideInput,
    reading_guide_workflow,
)
from omodul.receive_return import receive_return
from omodul.refund_payment import refund_payment
from omodul.regime_inference import (
    RegimeInferenceConfig,
    RegimeInferenceInput,
    regime_inference,
)
from omodul.regime_inference import (
    compute_fingerprint_for as compute_fingerprint_for_regime_inference,
)
from omodul.remove_discount_from_cart import (
    compute_fingerprint_for as remove_discount_from_cart_fingerprint,
)
from omodul.remove_discount_from_cart import remove_discount_from_cart
from omodul.remove_gift_card_from_cart import (
    compute_fingerprint_for as remove_gift_card_from_cart_fingerprint,
)
from omodul.remove_gift_card_from_cart import remove_gift_card_from_cart
from omodul.remove_prices_from_list import remove_prices_from_list
from omodul.reset_user_password import reset_user_password
from omodul.run_subagent_task import run_subagent_task
from omodul.set_cart_billing_address import (
    compute_fingerprint_for as set_cart_billing_address_fingerprint,
)
from omodul.set_cart_billing_address import set_cart_billing_address
from omodul.set_cart_customer import compute_fingerprint_for as set_cart_customer_fingerprint
from omodul.set_cart_customer import set_cart_customer
from omodul.set_cart_region import compute_fingerprint_for as set_cart_region_fingerprint
from omodul.set_cart_region import set_cart_region
from omodul.set_cart_shipping_address import (
    compute_fingerprint_for as set_cart_shipping_address_fingerprint,
)
from omodul.set_cart_shipping_address import set_cart_shipping_address
from omodul.set_payment_session import (
    compute_fingerprint_for as set_payment_session_fingerprint,
)
from omodul.set_payment_session import set_payment_session
from omodul.share_session import compute_fingerprint_for as share_session_fingerprint
from omodul.share_session import share_session
from omodul.ship_fulfillment import ship_fulfillment

# G7 教训→技能结晶 (KiroCrew 复刻)
from omodul.skill_crystallize import (
    skill_crystallize,
)
from omodul.strategy.daily_plan_generator import daily_plan_generate
from omodul.swarm_orchestrator import SwarmOrchestrator
from omodul.symbol_dim_score import (
    SymbolDimScoreConfig,
    SymbolDimScoreFindings,
    SymbolDimScoreInput,
    symbol_dim_score,
)
from omodul.symbol_dim_score import (
    compute_fingerprint_for as compute_fingerprint_for_symbol_dim_score,
)
from omodul.sync_models_catalog import compute_fingerprint_for as sync_models_catalog_fingerprint
from omodul.sync_models_catalog import sync_models_catalog
from omodul.task_manager import TaskManager
from omodul.threat_model_evolve import (
    SIGNAL_LIKELIHOODS,
    ThreatModelConfig,
    ThreatModelInput,
    threat_model_evolve,
)
from omodul.undo_changes import undo_changes
from omodul.unpublish_products_from_channel import unpublish_products_from_channel
from omodul.update_customer import compute_fingerprint_for as update_customer_fingerprint
from omodul.update_customer import update_customer
from omodul.update_customer_address import (
    compute_fingerprint_for as update_customer_address_fingerprint,
)
from omodul.update_customer_address import update_customer_address
from omodul.update_discount import compute_fingerprint_for as update_discount_fingerprint
from omodul.update_discount import update_discount
from omodul.update_discount_rule import (
    compute_fingerprint_for as update_discount_rule_fingerprint,
)
from omodul.update_discount_rule import update_discount_rule
from omodul.update_draft_order import update_draft_order
from omodul.update_gift_card import update_gift_card
from omodul.update_line_item_in_cart import (
    compute_fingerprint_for as update_line_item_in_cart_fingerprint,
)
from omodul.update_line_item_in_cart import update_line_item_in_cart
from omodul.update_order import update_order
from omodul.update_payment_sessions import (
    compute_fingerprint_for as update_payment_sessions_fingerprint,
)
from omodul.update_payment_sessions import update_payment_sessions
from omodul.update_price_list import update_price_list
from omodul.update_product import update_product
from omodul.update_product_category import update_product_category
from omodul.update_product_collection import update_product_collection
from omodul.update_product_option import update_product_option
from omodul.update_product_variant import update_product_variant
from omodul.update_region import compute_fingerprint_for as update_region_fingerprint
from omodul.update_region import update_region
from omodul.update_sales_channel import update_sales_channel
from omodul.update_stock_location import update_stock_location
from omodul.update_tax_rate import compute_fingerprint_for as update_tax_rate_fingerprint
from omodul.update_tax_rate import update_tax_rate
from omodul.update_user import update_user
from omodul.video_reliability_loop import (
    FailureKind as VideoFailureKind,
)

# ── 视频质检可靠性闭环 (同构 code_reliability_loop) ─────────────────────
# 注意: video 的 FailureKind 与 code 同名, 用别名导入避免覆盖。
from omodul.video_reliability_loop import (
    FailureSignature as VideoFailureSignature,
)
from omodul.video_reliability_loop import (
    VideoArtifact,
    VideoEvalResult,
    VideoLoopResult,
    VideoSpec,
    VideoTask,
    run_video_reliability_loop,
)

# 路径发现 WayfindingKernel (事件溯源, 同一套模式; 收敛完成后 decisions_to_runbook
# 桥接到 obase.orchestrator 的图状态机)
from omodul.wayfinding import (
    DecisionGist,
    Map,
    Ticket,
    WayfindingKernel,
    WayfindingKernelError,
    decisions_to_runbook,
    load_kernel,
    new_map_id,
    render_map_md,
    wayfinding_store,
)

# 路径发现 WayfindingKernel 的 GitHub Issues 后端 (map=issue, ticket=native
# sub-issue, blocking=native issue dependency — 与上面事件溯源版并存)
from omodul.wayfinding_github import (
    WayfindingGithubError,
    parse_map_body,
    render_map_body,
)
from omodul.wayfinding_github import (
    add_fog as gh_add_fog,
)
from omodul.wayfinding_github import (
    add_ticket as gh_add_ticket,
)
from omodul.wayfinding_github import (
    chart_map as gh_chart_map,
)
from omodul.wayfinding_github import (
    claim_ticket as gh_claim_ticket,
)
from omodul.wayfinding_github import (
    complete_if_clear as gh_complete_if_clear,
)
from omodul.wayfinding_github import (
    decisions_so_far as gh_decisions_so_far,
)
from omodul.wayfinding_github import (
    ensure_labels as gh_ensure_labels,
)
from omodul.wayfinding_github import (
    frontier as gh_frontier,
)
from omodul.wayfinding_github import (
    graduate_fog as gh_graduate_fog,
)
from omodul.wayfinding_github import (
    resolve_ticket as gh_resolve_ticket,
)
from omodul.wayfinding_github import (
    rule_out_of_scope as gh_rule_out_of_scope,
)
from omodul.wayfinding_github import (
    wire_blocking as gh_wire_blocking,
)
from omodul.web_research_task import web_research_task

from ._base import CostTracker, Trail
from ._base_config import BaseConfig
from .adaptive_quiz_session import (
    AdaptiveQuizConfig,
    AdaptiveQuizInput,
    adaptive_quiz_session,
)

# AutoAgent capability imports
from .agent_creation_workflow import agent_creation_workflow
from .agent_setup_workflow import agent_setup_workflow
from .apply_changeset import (
    ChangesetConfig,
    ChangesetInput,
    Edit,
    EditBlock,
    VersionStore,
    apply_changeset,
)
from .breakpoint_remediation_workflow import (
    BreakpointRemediationConfig,
    BreakpointRemediationInput,
    WrongQuestionEntry,
    breakpoint_remediation_workflow,
)
from .code_review import CodeReviewConfig, CodeReviewInput, code_review
from .compact_conversation import (
    CompactConversationConfig,
    CompactConversationInput,
    compact_conversation,
)
from .compute_fingerprint_for_generate_tests import compute_fingerprint_for_generate_tests
from .compute_fingerprint_for_initialize import compute_fingerprint_for_initialize
from .compute_fingerprint_for_run_subagent import (
    compute_fingerprint_for as compute_fingerprint_for_run_subagent,
)

# ── 防御底座 (loop breaker + folding) ────────────────────────────
from .context_compactor import context_compactor
from .create_checkpoint import CreateCheckpointConfig, CreateCheckpointInput, create_checkpoint
from .daily_mission_workflow import (
    DailyMissionConfig,
    DailyMissionInput,
    daily_mission_workflow,
)

# ── 决策智能 / 推理 / 溯源 (semantica 能力 3O 化) ─────────────────────
from .decision_ledger import (
    DecisionLedgerConfig,
    DecisionLedgerInput,
    decision_ledger,
)
from .due_recall_push import (
    DueRecallPushConfig,
    DueRecallPushInput,
    due_recall_push_workflow,
)

# ── 工程纪律门禁 (S1–S5 编排; Coordinator 只见 project_eng_gates) ─────
from .eng_gates import project_eng_gates
from .error_journal import (
    ErrorJournalConfig,
    ErrorJournalInput,
    error_journal_diagnostic,
)
from .evaluate_skill_version import SkillEvaluationConfig, evaluate_skill_version
from .execution_health_monitor import execution_health_monitor
from .explain_codebase import ExplainCodebaseConfig, ExplainCodebaseInput, explain_codebase
from .generate_commit_message import (
    GenerateCommitConfig,
    GenerateCommitInput,
    generate_commit_message,
)
from .generate_tests import GenerateTestsConfig, GenerateTestsInput, generate_tests
from .grade_paper_workflow import (
    GradePaperConfig,
    GradePaperInput,
    PaperQuestion,
    grade_paper_workflow,
)
from .implicit_feedback_processor import implicit_feedback_processor
from .initialize_project import InitProjectConfig, InitProjectInput, initialize_project
from .install_plugin import InstallPluginConfig, InstallPluginInput, install_plugin
from .instant_solve import (
    InstantSolveConfig,
    InstantSolveInput,
    instant_solve,
)
from .kg_reasoning import KgReasoningConfig, KgReasoningInput, kg_reasoning

# M-E: Mneme omodul elements
from .knowledge_profiling_workflow import (
    KnowledgeProfilingConfig,
    KnowledgeProfilingInput,
    knowledge_profiling_workflow,
)
from .ku_heal_cycle import KuHealCycleConfig, KuHealCycleInput, ku_heal_cycle

# ── 确定性知识库体检飞轮 (ku_lint / ku_health / ku_heal_cycle) ─────────
from .ku_health import list_open_issues, mark_resolved, persist_issues
from .ku_lint import KuLintConfig, KuLintInput, ku_lint
from .learning_progress_report import (
    LearningProgressConfig,
    ProgressInput,
    learning_progress_report,
)
from .migrate_dependency import MigrateDependencyConfig, MigrateDependencyInput, migrate_dependency
from .orchestrator_creation_workflow import orchestrator_creation_workflow
from .parent_review import (
    ParentReviewConfig,
    ParentReviewInput,
    parent_review_summary,
)
from .phase_closed_loop_plan import phase_closed_loop_plan
from .phase_evidence_verify import phase_evidence_verify
from .phase_intent_triage import phase_intent_triage
from .phase_spec_driven_plan import phase_spec_driven_plan
from .phase_verify_leaf_task import phase_verify_leaf_task
from .provenance_w3c import ProvenanceW3cConfig, ProvenanceW3cInput, provenance_w3c
from .qualify_change import ChangeQualificationConfig, qualify_change
from .refactor_transaction import (
    RefactorTransactionConfig,
    RefactorTransactionInput,
    refactor_transaction,
)
from .rewind_to_checkpoint import RewindConfig, RewindInput, rewind_to_checkpoint
from .run_and_fix import RunAndFixConfig, RunAndFixInput, run_and_fix

# ── 统一沙箱会话 (W1 环境合同; 多后端) ────────────────────────────
from .run_harness import run_harness
from .run_subagent import (
    HookSpec,
    SubagentConfig,
    SubagentDefinition,
    SubagentInput,
    SubagentPermissions,
    _current_cost,
    _current_depth,
    _current_trail,
    run_subagent,
)
from .sandbox_broker import SandboxBroker, get_broker, reset_broker, set_broker
from .sandbox_session import eval_in_sandbox, sandbox_scope, sandbox_session
from .security_audit import SecurityAuditConfig, SecurityAuditInput, security_audit
from .socratic_tutor_session import (
    SocraticTutorConfig,
    SocraticTutorInput,
    socratic_tutor_session,
)
from .summarize_session import SummarizeSessionConfig, SummarizeSessionInput, summarize_session
from .team_lifecycle_workflow import team_lifecycle_workflow
from .user_data_workflow import (
    UserDataConfig,
    UserDataInput,
    UserRecord,
    user_data_workflow,
)
from .variant_generation_workflow import (
    VariantGenerationConfig,
    VariantGenerationInput,
    VariantSource,
    variant_generation_workflow,
)

try:
    from . import analyze_paper as _analyze_paper
except ImportError:
    pass
else:
    AnalyzePaperConfig = _analyze_paper.AnalyzePaperConfig
    AnalyzePaperInput = _analyze_paper.AnalyzePaperInput
    analyze_paper_workflow = _analyze_paper.analyze_paper_workflow

# Aliases for backward compatibility or alternate names
InitializeConfig = InitProjectConfig
InitializeInput = InitProjectInput
CompactConfig = CompactConversationConfig
CompactInput = CompactConversationInput
ExplainConfig = ExplainCodebaseConfig
ExplainInput = ExplainCodebaseInput
CommitMsgConfig = GenerateCommitConfig
CommitMsgInput = GenerateCommitInput
MigrateConfig = MigrateDependencyConfig
MigrateInput = MigrateDependencyInput


# 统一的 compute_fingerprint_for(omodul_name, config, input_data) 路由
def compute_fingerprint_for(
    omodul_name: str | Any,
    config: Any,
    input_data: Any | None = None,
) -> str:
    # Preserve the established two-argument run_subagent helper while also
    # supporting the generic three-argument router.
    if input_data is None:
        input_data = config
        config = omodul_name
        omodul_name = "run_subagent"
    routers = {
        "initialize_project": compute_fingerprint_for_initialize,
        "run_subagent": compute_fingerprint_for_run_subagent,
        "generate_tests": compute_fingerprint_for_generate_tests,
        "symbol_dim_score": compute_fingerprint_for_symbol_dim_score,
        "regime_inference": compute_fingerprint_for_regime_inference,
        "candidate_pool": compute_fingerprint_for_candidate_pool,
    }
    if omodul_name not in routers:
        return ""
    return routers[omodul_name](config, input_data)


# Constants retained for the established run_subagent contract.
RECURSION_DEPTH_LIMIT = 5
RefactorConfig = RefactorTransactionConfig
RefactorInput = RefactorTransactionInput

# 3O canonical elements (one public class per element; ports/models remain in
# their canonical modules for explicit dependency injection).
from .accepted_progress_projection import AcceptedProgressProjection  # noqa: E402
from .code_knowledge_graph import CodeKnowledgeGraph  # noqa: E402
from .experiment_engine import ExperimentEngine  # noqa: E402

__all__ = [
    "ARTIFACT_TYPES",
    "AdaptiveQuizConfig",
    "AdaptiveQuizInput",
    "AdversarialChamberConfig",
    "AdversarialChamberInput",
    "Any",
    "_current_cost",
    "_current_depth",
    "_current_trail",
    "ApprovalGate",
    "AutomataScheduler",
    "BaseConfig",
    "BreakpointRemediationConfig",
    "BreakpointRemediationInput",
    "CandidatePoolConfig",
    "CandidatePoolInput",
    "CausalDiagnosisReport",
    "ChangeQualificationConfig",
    "ChangesetConfig",
    "ChangesetInput",
    "ClosedLoopConfig",
    "ClosedLoopInput",
    "CodeLoopResult",
    "CodeReviewConfig",
    "CodeReviewInput",
    "CodeTask",
    "CompactConversationConfig",
    "CompactConversationInput",
    "ConflictDetectionConfig",
    "ContinuousCholeskySCM",
    "ContinuousNode",
    "CostTracker",
    "CounterfactualDiagnosisReport",
    "CounterfactualReport",
    "CreateCheckpointConfig",
    "CreateCheckpointInput",
    "DEFAULT_HONEYPOT_ENV",
    "DEFAULT_SECRET_PATTERNS",
    "DailyMissionConfig",
    "DailyMissionInput",
    "DecisionGist",
    "DecisionLedgerConfig",
    "DecisionLedgerInput",
    "DueRecallPushConfig",
    "DueRecallPushInput",
    "EVENT_EVIDENCE_APPENDED",
    "EVENT_GATE_REQUIRED",
    "EVENT_GATE_RESOLVED",
    "EVENT_GOAL_ADDED",
    "EVENT_HANDOFF_RECORDED",
    "EVENT_TODO_UPDATED",
    "Edit",
    "EditBlock",
    "ErrorJournalConfig",
    "ErrorJournalInput",
    "Evidence",
    "ExecuteGovernedActionConfig",
    "ExecuteGovernedActionInput",
    "ExecutionResult",
    "ExplainCodebaseConfig",
    "ExplainCodebaseInput",
    "ExportSubstrateMarkdownConfig",
    "ExportSubstrateMarkdownInput",
    "FailureKind",
    "FailureSignature",
    "ForceAnalysisConfig",
    "ForceAnalysisInput",
    "Gate",
    "GenerateCommitConfig",
    "GenerateCommitInput",
    "GenerateTestsConfig",
    "GenerateTestsInput",
    "Goal",
    "GoalKernel",
    "GoalKernelError",
    "GovernActionConfig",
    "GovernActionInput",
    "GovernedMcpConfig",
    "GovernedMcpInput",
    "GovernedToolConfig",
    "GovernedToolInput",
    "GradePaperConfig",
    "GradePaperInput",
    "Handoff",
    "HoneypotObservation",
    "HoneypotSandboxResult",
    "HookSpec",
    "InitProjectConfig",
    "InitProjectInput",
    "InstallPluginConfig",
    "InstallPluginInput",
    "InstantSolveConfig",
    "InstantSolveInput",
    "IntegrityResult",
    "KgReasoningConfig",
    "KgReasoningInput",
    "KnowledgeProfilingConfig",
    "KnowledgeProfilingInput",
    "KuHealCycleConfig",
    "KuHealCycleInput",
    "KuLintConfig",
    "KuLintInput",
    "LearningProgressConfig",
    "Map",
    "MigrateDependencyConfig",
    "MigrateDependencyInput",
    "ModelRouter",
    "MultiStepPlanReport",
    "NeuroSymbolicResult",
    "NodeInterventionResult",
    "ObserverConfig",
    "OperatorDecision",
    "OperatorEscalation",
    "PaperQuestion",
    "ParentReviewConfig",
    "ParentReviewInput",
    "PatchArtifact",
    "PrepareBrowserSessionConfig",
    "PrepareBrowserSessionInput",
    "PrepareComputerSessionConfig",
    "PrepareComputerSessionInput",
    "ProgressInput",
    "ProvenanceW3cConfig",
    "ProvenanceW3cInput",
    "ProviderInferenceConfig",
    "ProviderInferenceInput",
    "QuotaView",
    "ReadingGuideConfig",
    "ReadingGuideInput",
    "RefactorTransactionConfig",
    "RefactorTransactionInput",
    "RegimeInferenceConfig",
    "RegimeInferenceInput",
    "RepairPayload",
    "RewindConfig",
    "RewindInput",
    "RunAndFixConfig",
    "RunAndFixInput",
    "SIGNAL_LIKELIHOODS",
    "SandboxBroker",
    "SecurityAuditConfig",
    "SecurityAuditInput",
    "SkillEvaluationConfig",
    "SocraticTutorConfig",
    "SocraticTutorInput",
    "SubagentConfig",
    "SubagentDefinition",
    "SubagentInput",
    "SubagentPermissions",
    "SummarizeSessionConfig",
    "SummarizeSessionInput",
    "SupervisorPolicy",
    "SwarmOrchestrator",
    "SymbolDimScoreConfig",
    "SymbolDimScoreFindings",
    "SymbolDimScoreInput",
    "TaskManager",
    "TestResult",
    "ThreatModelConfig",
    "ThreatModelInput",
    "Ticket",
    "Todo",
    "Trail",
    "UserDataConfig",
    "UserDataInput",
    "UserRecord",
    "VariantGenerationConfig",
    "VariantGenerationInput",
    "VariantSource",
    "VersionStore",
    "VideoArtifact",
    "VideoEvalResult",
    "VideoFailureKind",
    "VideoFailureSignature",
    "VideoLoopResult",
    "VideoSpec",
    "VideoTask",
    "WayfindingGithubError",
    "WayfindingKernel",
    "WayfindingKernelError",
    "WorktreeState",
    "WrongQuestionEntry",
    "adaptive_quiz_session",
    "add_customer_address",
    "add_customer_address_fingerprint",
    "add_line_item_to_cart",
    "add_line_item_to_cart_fingerprint",
    "add_prices_to_list",
    "add_shipping_method_to_cart",
    "add_shipping_method_to_cart_fingerprint",
    "adjust_inventory_level",
    "adversarial_chamber",
    "adversarial_honeypot_observe",
    "agent_creation_workflow",
    "agent_setup_workflow",
    "apply_changeset",
    "apply_discount_to_cart",
    "apply_discount_to_cart_fingerprint",
    "apply_gift_card_to_cart",
    "apply_gift_card_to_cart_fingerprint",
    "archive_order",
    "artifact_preview",
    "assign_customer_to_group",
    "assign_customer_to_group_fingerprint",
    "authorize_payment_for_cart",
    "authorize_payment_for_cart_fingerprint",
    "breakpoint_remediation_workflow",
    "cancel_batch_job",
    "cancel_claim",
    "cancel_fulfillment",
    "cancel_order",
    "cancel_return",
    "cancel_swap",
    "candidate_pool",
    "capture_payment",
    "causal_fault_diagnose",
    "closed_loop_intervene",
    "code_review",
    "compact_conversation",
    "compact_session",
    "compact_session_fingerprint",
    "complete_checkout",
    "complete_checkout_fingerprint",
    "compute_fingerprint_for_candidate_pool",
    "compute_fingerprint_for_generate_tests",
    "compute_fingerprint_for_initialize",
    "compute_fingerprint_for_regime_inference",
    "compute_fingerprint_for_run_subagent",
    "compute_fingerprint_for_symbol_dim_score",
    "compute_plan_id",
    "conflict_detection_workflow",
    "context_compactor",
    "counterfactual_diagnose",
    "create_batch_job",
    "create_cart",
    "create_cart_fingerprint",
    "create_checkpoint",
    "create_claim",
    "create_customer",
    "create_customer_fingerprint",
    "create_customer_group",
    "create_customer_group_fingerprint",
    "create_discount",
    "create_discount_condition",
    "create_discount_condition_fingerprint",
    "create_discount_fingerprint",
    "create_discount_rule",
    "create_discount_rule_fingerprint",
    "create_draft_order",
    "create_fulfillment",
    "create_gift_card",
    "create_inventory_batch",
    "create_inventory_batch_fingerprint",
    "create_payment_sessions",
    "create_payment_sessions_fingerprint",
    "create_price_list",
    "create_product",
    "create_product_category",
    "create_product_collection",
    "create_product_option",
    "create_product_variant",
    "create_region",
    "create_region_fingerprint",
    "create_return_request",
    "create_sales_channel",
    "create_session",
    "create_session_fingerprint",
    "create_stock_location",
    "create_swap",
    "create_tax_rate",
    "create_tax_rate_fingerprint",
    "create_user",
    "daily_mission_workflow",
    "daily_plan_generate",
    "decision_ledger",
    "decisions_to_runbook",
    "delete_customer_address",
    "delete_customer_address_fingerprint",
    "delete_discount",
    "delete_discount_condition",
    "delete_discount_condition_fingerprint",
    "delete_discount_fingerprint",
    "delete_draft_order",
    "delete_gift_card",
    "delete_line_item_from_cart",
    "delete_line_item_from_cart_fingerprint",
    "delete_price_list",
    "delete_product",
    "delete_product_category",
    "delete_product_collection",
    "delete_product_option",
    "delete_product_variant",
    "delete_region",
    "delete_region_fingerprint",
    "delete_sales_channel",
    "delete_stock_location",
    "delete_tax_rate",
    "delete_tax_rate_fingerprint",
    "due_recall_push_workflow",
    "error_journal_diagnostic",
    "eval_in_sandbox",
    "evaluate_skill_version",
    "execute_governed_action",
    "execute_tool",
    "execution_health_monitor",
    "explain_codebase",
    "export_substrate_markdown",
    "force_analysis_workflow",
    "fork_session",
    "fork_session_fingerprint",
    "fulfill_claim",
    "fulfill_swap",
    "generate_commit_message",
    "generate_tests",
    "get_broker",
    "gh_add_fog",
    "gh_add_ticket",
    "gh_chart_map",
    "gh_claim_ticket",
    "gh_complete_if_clear",
    "gh_decisions_so_far",
    "gh_ensure_labels",
    "gh_frontier",
    "gh_graduate_fog",
    "gh_resolve_ticket",
    "gh_rule_out_of_scope",
    "gh_wire_blocking",
    "govern_action",
    "governed_mcp_transaction",
    "governed_tool_transaction",
    "grade_paper_workflow",
    "implicit_feedback_processor",
    "index_codebase",
    "index_codebase_fingerprint",
    "init_project",
    "initialize_project",
    "install_plugin",
    "instant_solve",
    "kg_reasoning",
    "knowledge_profiling_workflow",
    "ku_heal_cycle",
    "ku_lint",
    "learning_progress_report",
    "list_open_issues",
    "load_kernel",
    "login_provider",
    "mark_draft_order_paid",
    "mark_resolved",
    "migrate_dependency",
    "mission_revert",
    "mission_supervisor",
    "multi_step_plan",
    "new_map_id",
    "orchestrator_creation_workflow",
    "parent_review_summary",
    "parse_diff",
    "parse_map_body",
    "persist_issues",
    "phase_closed_loop_plan",
    "phase_evidence_verify",
    "phase_intent_triage",
    "phase_spec_driven_plan",
    "phase_verify_leaf_task",
    "prepare_browser_session",
    "prepare_computer_session",
    "process_prompt",
    "process_swap_payment",
    "project_eng_gates",
    "provenance_w3c",
    "provider_inference_transaction",
    "publish_products_to_channel",
    "qualify_change",
    "reading_guide_workflow",
    "receive_return",
    "refactor_transaction",
    "refund_payment",
    "regime_inference",
    "remove_discount_from_cart",
    "remove_discount_from_cart_fingerprint",
    "remove_gift_card_from_cart",
    "remove_gift_card_from_cart_fingerprint",
    "remove_prices_from_list",
    "render_decision",
    "render_map_body",
    "render_map_md",
    "reset_broker",
    "reset_user_password",
    "rewind_to_checkpoint",
    "run_and_fix",
    "run_code_reliability_loop",
    "run_harness",
    "run_neuro_symbolic",
    "run_observer_lookahead",
    "run_operator_center",
    "run_subagent",
    "run_subagent_task",
    "run_video_reliability_loop",
    "sandbox_scope",
    "sandbox_session",
    "sanitize_markup",
    "security_audit",
    "set_broker",
    "set_cart_billing_address",
    "set_cart_billing_address_fingerprint",
    "set_cart_customer",
    "set_cart_customer_fingerprint",
    "set_cart_region",
    "set_cart_region_fingerprint",
    "set_cart_shipping_address",
    "set_cart_shipping_address_fingerprint",
    "set_payment_session",
    "set_payment_session_fingerprint",
    "share_session",
    "share_session_fingerprint",
    "ship_fulfillment",
    "skill_crystallize",
    "snapshot_mission_baseline",
    "socratic_tutor_session",
    "summarize_session",
    "symbol_dim_score",
    "sync_models_catalog",
    "sync_models_catalog_fingerprint",
    "team_lifecycle_workflow",
    "threat_model_evolve",
    "undo_changes",
    "unpublish_products_from_channel",
    "update_cpd_from_repair",
    "update_customer",
    "update_customer_address",
    "update_customer_address_fingerprint",
    "update_customer_fingerprint",
    "update_discount",
    "update_discount_fingerprint",
    "update_discount_rule",
    "update_discount_rule_fingerprint",
    "update_draft_order",
    "update_gift_card",
    "update_line_item_in_cart",
    "update_line_item_in_cart_fingerprint",
    "update_order",
    "update_payment_sessions",
    "update_payment_sessions_fingerprint",
    "update_price_list",
    "update_product",
    "update_product_category",
    "update_product_collection",
    "update_product_option",
    "update_product_variant",
    "update_region",
    "update_region_fingerprint",
    "update_sales_channel",
    "update_stock_location",
    "update_tax_rate",
    "update_tax_rate_fingerprint",
    "update_user",
    "user_data_workflow",
    "variant_generation_workflow",
    "wayfinding_store",
    "web_research_task",
]

__all__ += [
    "AcceptedProgressProjection",
    "CodeKnowledgeGraph",
    "ExperimentEngine",
]
