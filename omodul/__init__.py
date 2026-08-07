__version__ = "1.49.0"
from typing import Any

# New omodul modules (batch 1.29)
from omodul.process_prompt import process_prompt
from omodul.execute_tool import execute_tool
from omodul.compact_session import compact_session
from omodul.compact_session import compute_fingerprint_for as compact_session_fingerprint
from omodul.init_project import init_project
from omodul.create_session import create_session
from omodul.create_session import compute_fingerprint_for as create_session_fingerprint
from omodul.fork_session import fork_session
from omodul.fork_session import compute_fingerprint_for as fork_session_fingerprint
from omodul.share_session import share_session
from omodul.share_session import compute_fingerprint_for as share_session_fingerprint
from omodul.login_provider import login_provider
from omodul.run_subagent_task import run_subagent_task
from omodul.web_research_task import web_research_task
from omodul.undo_changes import undo_changes
from omodul.sync_models_catalog import sync_models_catalog
from omodul.sync_models_catalog import compute_fingerprint_for as sync_models_catalog_fingerprint
from omodul.index_codebase import index_codebase
from omodul.index_codebase import compute_fingerprint_for as index_codebase_fingerprint

# Batch-warehouse commerce vertical
from omodul.create_inventory_batch import create_inventory_batch
from omodul.create_inventory_batch import (
    compute_fingerprint_for as create_inventory_batch_fingerprint,
)
from omodul.add_line_item_to_cart import add_line_item_to_cart
from omodul.add_line_item_to_cart import (
    compute_fingerprint_for as add_line_item_to_cart_fingerprint,
)
from omodul.create_cart import create_cart
from omodul.create_cart import compute_fingerprint_for as create_cart_fingerprint
from omodul.set_cart_region import set_cart_region
from omodul.set_cart_region import compute_fingerprint_for as set_cart_region_fingerprint
from omodul.set_cart_customer import set_cart_customer
from omodul.set_cart_customer import compute_fingerprint_for as set_cart_customer_fingerprint
from omodul.update_line_item_in_cart import update_line_item_in_cart
from omodul.update_line_item_in_cart import (
    compute_fingerprint_for as update_line_item_in_cart_fingerprint,
)
from omodul.delete_line_item_from_cart import delete_line_item_from_cart
from omodul.delete_line_item_from_cart import (
    compute_fingerprint_for as delete_line_item_from_cart_fingerprint,
)
from omodul.create_discount import create_discount
from omodul.create_discount import compute_fingerprint_for as create_discount_fingerprint
from omodul.update_discount import update_discount
from omodul.update_discount import compute_fingerprint_for as update_discount_fingerprint
from omodul.delete_discount import delete_discount
from omodul.delete_discount import compute_fingerprint_for as delete_discount_fingerprint
from omodul.create_discount_rule import create_discount_rule
from omodul.create_discount_rule import (
    compute_fingerprint_for as create_discount_rule_fingerprint,
)
from omodul.update_discount_rule import update_discount_rule
from omodul.update_discount_rule import (
    compute_fingerprint_for as update_discount_rule_fingerprint,
)
from omodul.create_discount_condition import create_discount_condition
from omodul.create_discount_condition import (
    compute_fingerprint_for as create_discount_condition_fingerprint,
)
from omodul.delete_discount_condition import delete_discount_condition
from omodul.delete_discount_condition import (
    compute_fingerprint_for as delete_discount_condition_fingerprint,
)
from omodul.create_gift_card import create_gift_card
from omodul.update_gift_card import update_gift_card
from omodul.delete_gift_card import delete_gift_card
from omodul.apply_discount_to_cart import apply_discount_to_cart
from omodul.apply_discount_to_cart import (
    compute_fingerprint_for as apply_discount_to_cart_fingerprint,
)
from omodul.remove_discount_from_cart import remove_discount_from_cart
from omodul.remove_discount_from_cart import (
    compute_fingerprint_for as remove_discount_from_cart_fingerprint,
)
from omodul.apply_gift_card_to_cart import apply_gift_card_to_cart
from omodul.apply_gift_card_to_cart import (
    compute_fingerprint_for as apply_gift_card_to_cart_fingerprint,
)
from omodul.remove_gift_card_from_cart import remove_gift_card_from_cart
from omodul.remove_gift_card_from_cart import (
    compute_fingerprint_for as remove_gift_card_from_cart_fingerprint,
)
from omodul.set_cart_billing_address import set_cart_billing_address
from omodul.set_cart_billing_address import (
    compute_fingerprint_for as set_cart_billing_address_fingerprint,
)
from omodul.set_cart_shipping_address import set_cart_shipping_address
from omodul.set_cart_shipping_address import (
    compute_fingerprint_for as set_cart_shipping_address_fingerprint,
)
from omodul.add_shipping_method_to_cart import add_shipping_method_to_cart
from omodul.add_shipping_method_to_cart import (
    compute_fingerprint_for as add_shipping_method_to_cart_fingerprint,
)
from omodul.create_payment_sessions import create_payment_sessions
from omodul.create_payment_sessions import (
    compute_fingerprint_for as create_payment_sessions_fingerprint,
)
from omodul.update_payment_sessions import update_payment_sessions
from omodul.update_payment_sessions import (
    compute_fingerprint_for as update_payment_sessions_fingerprint,
)
from omodul.set_payment_session import set_payment_session
from omodul.set_payment_session import (
    compute_fingerprint_for as set_payment_session_fingerprint,
)
from omodul.authorize_payment_for_cart import authorize_payment_for_cart
from omodul.authorize_payment_for_cart import (
    compute_fingerprint_for as authorize_payment_for_cart_fingerprint,
)
from omodul.complete_checkout import complete_checkout
from omodul.complete_checkout import (
    compute_fingerprint_for as complete_checkout_fingerprint,
)
from omodul.update_order import update_order
from omodul.cancel_order import cancel_order
from omodul.archive_order import archive_order
from omodul.create_draft_order import create_draft_order
from omodul.update_draft_order import update_draft_order
from omodul.delete_draft_order import delete_draft_order
from omodul.mark_draft_order_paid import mark_draft_order_paid
from omodul.create_region import create_region
from omodul.create_region import compute_fingerprint_for as create_region_fingerprint
from omodul.update_region import update_region
from omodul.update_region import compute_fingerprint_for as update_region_fingerprint
from omodul.delete_region import delete_region
from omodul.delete_region import compute_fingerprint_for as delete_region_fingerprint
from omodul.create_tax_rate import create_tax_rate
from omodul.create_tax_rate import compute_fingerprint_for as create_tax_rate_fingerprint
from omodul.update_tax_rate import update_tax_rate
from omodul.update_tax_rate import compute_fingerprint_for as update_tax_rate_fingerprint
from omodul.delete_tax_rate import delete_tax_rate
from omodul.delete_tax_rate import compute_fingerprint_for as delete_tax_rate_fingerprint
from omodul.create_user import create_user
from omodul.update_user import update_user
from omodul.reset_user_password import reset_user_password
from omodul.create_customer import create_customer
from omodul.create_customer import compute_fingerprint_for as create_customer_fingerprint
from omodul.update_customer import update_customer
from omodul.update_customer import compute_fingerprint_for as update_customer_fingerprint
from omodul.add_customer_address import add_customer_address
from omodul.add_customer_address import (
    compute_fingerprint_for as add_customer_address_fingerprint,
)
from omodul.update_customer_address import update_customer_address
from omodul.update_customer_address import (
    compute_fingerprint_for as update_customer_address_fingerprint,
)
from omodul.delete_customer_address import delete_customer_address
from omodul.delete_customer_address import (
    compute_fingerprint_for as delete_customer_address_fingerprint,
)
from omodul.create_customer_group import create_customer_group
from omodul.create_customer_group import (
    compute_fingerprint_for as create_customer_group_fingerprint,
)
from omodul.assign_customer_to_group import assign_customer_to_group
from omodul.assign_customer_to_group import (
    compute_fingerprint_for as assign_customer_to_group_fingerprint,
)
from omodul.create_product import create_product
from omodul.update_product import update_product
from omodul.delete_product import delete_product
from omodul.create_product_variant import create_product_variant
from omodul.update_product_variant import update_product_variant
from omodul.delete_product_variant import delete_product_variant
from omodul.create_product_option import create_product_option
from omodul.update_product_option import update_product_option
from omodul.delete_product_option import delete_product_option
from omodul.create_product_category import create_product_category
from omodul.update_product_category import update_product_category
from omodul.delete_product_category import delete_product_category
from omodul.create_product_collection import create_product_collection
from omodul.update_product_collection import update_product_collection
from omodul.delete_product_collection import delete_product_collection
from omodul.create_price_list import create_price_list
from omodul.update_price_list import update_price_list
from omodul.delete_price_list import delete_price_list
from omodul.add_prices_to_list import add_prices_to_list
from omodul.remove_prices_from_list import remove_prices_from_list
from omodul.create_stock_location import create_stock_location
from omodul.update_stock_location import update_stock_location
from omodul.delete_stock_location import delete_stock_location
from omodul.adjust_inventory_level import adjust_inventory_level
from omodul.create_sales_channel import create_sales_channel
from omodul.update_sales_channel import update_sales_channel
from omodul.delete_sales_channel import delete_sales_channel
from omodul.publish_products_to_channel import publish_products_to_channel
from omodul.unpublish_products_from_channel import unpublish_products_from_channel
from omodul.capture_payment import capture_payment
from omodul.refund_payment import refund_payment
from omodul.create_fulfillment import create_fulfillment
from omodul.cancel_fulfillment import cancel_fulfillment
from omodul.ship_fulfillment import ship_fulfillment
from omodul.create_return_request import create_return_request
from omodul.receive_return import receive_return
from omodul.cancel_return import cancel_return
from omodul.create_swap import create_swap
from omodul.cancel_swap import cancel_swap
from omodul.fulfill_swap import fulfill_swap
from omodul.process_swap_payment import process_swap_payment
from omodul.create_claim import create_claim
from omodul.cancel_claim import cancel_claim
from omodul.fulfill_claim import fulfill_claim
from omodul.create_batch_job import create_batch_job
from omodul.cancel_batch_job import cancel_batch_job

from ._base_config import BaseConfig
from .compute_fingerprint_for_initialize import compute_fingerprint_for_initialize
from .compute_fingerprint_for_run_subagent import (
    compute_fingerprint_for as compute_fingerprint_for_run_subagent,
)
from .compute_fingerprint_for_generate_tests import compute_fingerprint_for_generate_tests

from ._base import CostTracker, Trail
from .apply_changeset import (
    ChangesetConfig,
    ChangesetInput,
    apply_changeset,
    Edit,
    EditBlock,
    VersionStore,
)
from .run_subagent import SubagentConfig, SubagentInput, run_subagent
from .initialize_project import InitProjectConfig, InitProjectInput, initialize_project
from .create_checkpoint import CreateCheckpointConfig, CreateCheckpointInput, create_checkpoint
from .rewind_to_checkpoint import RewindConfig, RewindInput, rewind_to_checkpoint
from .run_and_fix import RunAndFixConfig, RunAndFixInput, run_and_fix
from .code_review import CodeReviewConfig, CodeReviewInput, code_review
from .explain_codebase import ExplainCodebaseConfig, ExplainCodebaseInput, explain_codebase
from .generate_commit_message import (
    GenerateCommitConfig,
    GenerateCommitInput,
    generate_commit_message,
)
from .generate_tests import GenerateTestsConfig, GenerateTestsInput, generate_tests
from .summarize_session import SummarizeSessionConfig, SummarizeSessionInput, summarize_session
from .compact_conversation import (
    CompactConversationConfig,
    CompactConversationInput,
    compact_conversation,
)
from .security_audit import SecurityAuditConfig, SecurityAuditInput, security_audit
from .migrate_dependency import MigrateDependencyConfig, MigrateDependencyInput, migrate_dependency
from .refactor_transaction import (
    RefactorTransactionConfig,
    RefactorTransactionInput,
    refactor_transaction,
)
from .install_plugin import InstallPluginConfig, InstallPluginInput, install_plugin

# M-E: Mneme omodul elements
from .knowledge_profiling_workflow import (
    KnowledgeProfilingConfig,
    KnowledgeProfilingInput,
    knowledge_profiling_workflow,
)
from .adaptive_quiz_session import (
    AdaptiveQuizConfig,
    AdaptiveQuizInput,
    adaptive_quiz_session,
)
from .socratic_tutor_session import (
    SocraticTutorConfig,
    SocraticTutorInput,
    socratic_tutor_session,
)
from .grade_paper_workflow import (
    GradePaperConfig,
    GradePaperInput,
    PaperQuestion,
    grade_paper_workflow,
)

try:
    from .analyze_paper import (
        AnalyzePaperConfig,
        AnalyzePaperInput,
        analyze_paper_workflow,
    )
except ImportError:
    pass
from .daily_mission_workflow import (
    DailyMissionConfig,
    DailyMissionInput,
    daily_mission_workflow,
)
from .instant_solve import (
    InstantSolveConfig,
    InstantSolveInput,
    instant_solve,
)
from .error_journal import (
    ErrorJournalConfig,
    ErrorJournalInput,
    error_journal_diagnostic,
)
from .due_recall_push import (
    DueRecallPushConfig,
    DueRecallPushInput,
    due_recall_push_workflow,
)
from .parent_review import (
    ParentReviewConfig,
    ParentReviewInput,
    parent_review_summary,
)
from .variant_generation_workflow import (
    VariantGenerationConfig,
    VariantGenerationInput,
    VariantSource,
    variant_generation_workflow,
)
from .learning_progress_report import (
    LearningProgressConfig,
    ProgressInput,
    learning_progress_report,
)
from .breakpoint_remediation_workflow import (
    BreakpointRemediationConfig,
    BreakpointRemediationInput,
    WrongQuestionEntry,
    breakpoint_remediation_workflow,
)
from .user_data_workflow import (
    UserDataConfig,
    UserDataInput,
    UserRecord,
    user_data_workflow,
)

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

# --- Tide A股 re-export 复原（R1）：历史导出过的 3 个 + smoke 需要的 daily_plan_generate ---
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
from omodul.strategy.daily_plan_generator import daily_plan_generate


# 统一的 compute_fingerprint_for(omodul_name, config, input_data) 路由
def compute_fingerprint_for(omodul_name: str, config: Any, input_data: Any) -> str:
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


# Constants and extra classes for test compatibility
RECURSION_DEPTH_LIMIT = 5
RefactorConfig = RefactorTransactionConfig
RefactorInput = RefactorTransactionInput

from dataclasses import dataclass, field


@dataclass
class SubagentDefinition:
    name: str
    description: str
    instructions: str
    tools: list[str] = field(default_factory=list)


@dataclass
class SubagentPermissions:
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    max_usd: float = 1.0


@dataclass
class HookSpec:
    event: str
    command: str
    matcher: str | None = None


from contextvars import ContextVar

_current_cost: ContextVar[float] = ContextVar("_current_cost", default=0.0)
_current_depth: ContextVar[int] = ContextVar("_current_depth", default=0)

from omodul.export_substrate_markdown import (
    export_substrate_markdown,
    ExportSubstrateMarkdownConfig,
    ExportSubstrateMarkdownInput,
)

from omodul.force_analysis_workflow import (
    force_analysis_workflow,
    ForceAnalysisConfig,
    ForceAnalysisInput,
)
from omodul.reading_guide_workflow import (
    reading_guide_workflow,
    ReadingGuideConfig,
    ReadingGuideInput,
)

# ── AII Conflict Detection Workflow (M-G1) ───────────────────────────────────
from omodul.conflict_detection_workflow import conflict_detection_workflow, ConflictDetectionConfig

# AutoAgent capability imports
from .agent_creation_workflow import agent_creation_workflow  # noqa: F401
from .orchestrator_creation_workflow import orchestrator_creation_workflow  # noqa: F401

from .team_lifecycle_workflow import team_lifecycle_workflow  # noqa: F401

from .agent_setup_workflow import agent_setup_workflow  # noqa: F401

# cindy_mcp_server available via lazy import

# ── Veya Agent OS 编排层 (P3 固化) ────────────────────────────────────────
from omodul.automata import AutomataScheduler  # noqa: F401
from omodul.hitl_approval import ApprovalGate  # noqa: F401
from omodul.swarm_orchestrator import SwarmOrchestrator  # noqa: F401

from omodul.task_manager import TaskManager  # noqa: F401
from omodul.model_router import ModelRouter  # noqa: F401

# ── 红蓝对抗审判庭 (高阶审判模块) ────────────────────────────────────
from omodul.adversarial_chamber import (  # noqa: F401
    AdversarialChamberConfig,
    AdversarialChamberInput,
    adversarial_chamber,
)

# ── 神经符号 / 组合优化 / 沙箱推演 编排管线 (O1/O2/O3) ───────────────────
from omodul.neuro_symbolic import (  # noqa: F401
    NeuroSymbolicResult,
    RepairPayload,
    compute_plan_id,
    run_neuro_symbolic,
)
from omodul.operator_center import (  # noqa: F401
    OperatorDecision,
    OperatorEscalation,
    render_decision,
    run_operator_center,
)
from omodul.observer import ObserverConfig, run_observer_lookahead  # noqa: F401

# ── Phase 3: 反脆弱闭环 (最优干预 / 在线因果更新 / 威胁演化) ────────────
from omodul.closed_loop_intervene import (  # noqa: F401
    ClosedLoopConfig,
    ClosedLoopInput,
    closed_loop_intervene,
)
from omodul.threat_model_evolve import (  # noqa: F401
    SIGNAL_LIKELIHOODS,
    ThreatModelConfig,
    ThreatModelInput,
    threat_model_evolve,
)

# ── Phase 2: 因果诊断 (O1) + 蜜罐反间谍 (O3) 事务 ─────────────────────
from omodul.causal_fault_diagnose import (  # noqa: F401
    CausalDiagnosisReport,
    NodeInterventionResult,
    causal_fault_diagnose,
)
from omodul.adversarial_honeypot_observe import (  # noqa: F401
    DEFAULT_HONEYPOT_ENV,
    HoneypotObservation,
    HoneypotSandboxResult,
    adversarial_honeypot_observe,
)

# ── Phase 4: 多步反事实规划事务 ───────────────────────────────────────
from omodul.multi_step_plan import (  # noqa: F401
    ExecutionResult,
    MultiStepPlanReport,
    multi_step_plan,
    update_cpd_from_repair,
)

# ── Phase 4 延伸: L3 反事实诊断事务 (针对本次故障) ─────────────────────
from omodul.counterfactual_diagnose import (  # noqa: F401
    CounterfactualDiagnosisReport,
    CounterfactualReport,
    counterfactual_diagnose,
)

# ── 连续 Cholesky SCM (多维遥测 L3 溯因) ────────────────────────────────
from omodul.cholesky_scm import ContinuousCholeskySCM, ContinuousNode  # noqa: F401

# ── 代码 Agent 可靠性闭环 (方案 A+C) ─────────────────────────────────────
from omodul.code_reliability_loop import (  # noqa: F401
    CodeLoopResult,
    CodeTask,
    FailureKind,
    FailureSignature,
    PatchArtifact,
    TestResult,
    run_code_reliability_loop,
)

# ── 视频质检可靠性闭环 (同构 code_reliability_loop) ─────────────────────
# 注意: video 的 FailureKind 与 code 同名, 用别名导入避免覆盖。
from omodul.video_reliability_loop import (  # noqa: F401
    FailureSignature as VideoFailureSignature,
    VideoArtifact,
    VideoEvalResult,
    VideoLoopResult,
    VideoSpec,
    VideoTask,
    FailureKind as VideoFailureKind,
    run_video_reliability_loop,
)

# G3/G4 Vigla 复刻: Merge 审计 + Mission 回滚
from omodul.mission_supervisor import (  # noqa: E402
    DEFAULT_SECRET_PATTERNS,
    SupervisorPolicy,
    mission_supervisor,
    parse_diff,
)
from omodul.mission_revert import (  # noqa: E402
    WorktreeState,
    mission_revert,
    snapshot_mission_baseline,
)
