__version__ = "1.33.1"
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

from ._base_config import BaseConfig
from .compute_fingerprint_for_initialize import compute_fingerprint_for_initialize
from .compute_fingerprint_for_run_subagent import compute_fingerprint_for as compute_fingerprint_for_run_subagent
from .compute_fingerprint_for_generate_tests import compute_fingerprint_for_generate_tests

from ._base import CostTracker, Trail
from .apply_changeset import ChangesetConfig, ChangesetInput, apply_changeset, Edit, EditBlock, VersionStore
from .run_subagent import SubagentConfig, SubagentInput, run_subagent
from .initialize_project import InitProjectConfig, InitProjectInput, initialize_project
from .create_checkpoint import CreateCheckpointConfig, CreateCheckpointInput, create_checkpoint
from .rewind_to_checkpoint import RewindConfig, RewindInput, rewind_to_checkpoint
from .run_and_fix import RunAndFixConfig, RunAndFixInput, run_and_fix
from .code_review import CodeReviewConfig, CodeReviewInput, code_review
from .explain_codebase import ExplainCodebaseConfig, ExplainCodebaseInput, explain_codebase
from .generate_commit_message import GenerateCommitConfig, GenerateCommitInput, generate_commit_message
from .generate_tests import GenerateTestsConfig, GenerateTestsInput, generate_tests
from .summarize_session import SummarizeSessionConfig, SummarizeSessionInput, summarize_session
from .compact_conversation import CompactConversationConfig, CompactConversationInput, compact_conversation
from .security_audit import SecurityAuditConfig, SecurityAuditInput, security_audit
from .migrate_dependency import MigrateDependencyConfig, MigrateDependencyInput, migrate_dependency
from .refactor_transaction import RefactorTransactionConfig, RefactorTransactionInput, refactor_transaction
from .install_plugin import InstallPluginConfig, InstallPluginInput, install_plugin

# M-E: Mneme omodul elements
from .knowledge_profiling_workflow import (
    KnowledgeProfilingConfig, KnowledgeProfilingInput, knowledge_profiling_workflow,
)
from .adaptive_quiz_session import (
    AdaptiveQuizConfig, AdaptiveQuizInput, adaptive_quiz_session,
)
from .socratic_tutor_session import (
    SocraticTutorConfig, SocraticTutorInput, socratic_tutor_session,
)
from .grade_paper_workflow import (
    GradePaperConfig, GradePaperInput, PaperQuestion, grade_paper_workflow,
)
try:
    from .analyze_paper import (
        AnalyzePaperConfig, AnalyzePaperInput, analyze_paper_workflow,
    )
except ImportError:
    pass
from .daily_mission_workflow import (
    DailyMissionConfig, DailyMissionInput, daily_mission_workflow,
)
from .instant_solve import (
    InstantSolveConfig, InstantSolveInput, instant_solve,
)
from .error_journal import (
    ErrorJournalConfig, ErrorJournalInput, error_journal_diagnostic,
)
from .due_recall_push import (
    DueRecallPushConfig, DueRecallPushInput, due_recall_push_workflow,
)
from .parent_review import (
    ParentReviewConfig, ParentReviewInput, parent_review_summary,
)
from .variant_generation_workflow import (
    VariantGenerationConfig, VariantGenerationInput, VariantSource, variant_generation_workflow,
)
from .learning_progress_report import (
    LearningProgressConfig, ProgressInput, learning_progress_report,
)
from .breakpoint_remediation_workflow import (
    BreakpointRemediationConfig, BreakpointRemediationInput,
    WrongQuestionEntry, breakpoint_remediation_workflow,
)
from .user_data_workflow import (
    UserDataConfig, UserDataInput, UserRecord, user_data_workflow,
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

# 统一的 compute_fingerprint_for(omodul_name, config, input_data) 路由
def compute_fingerprint_for(omodul_name: str, config: Any, input_data: Any) -> str:
    routers = {
        "initialize_project": compute_fingerprint_for_initialize,
        "run_subagent": compute_fingerprint_for_run_subagent,
        "generate_tests": compute_fingerprint_for_generate_tests,
    }
    if omodul_name not in routers: return ""
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

from omodul.export_substrate_markdown import export_substrate_markdown, ExportSubstrateMarkdownConfig, ExportSubstrateMarkdownInput

from omodul.force_analysis_workflow import force_analysis_workflow, ForceAnalysisConfig, ForceAnalysisInput
from omodul.reading_guide_workflow import reading_guide_workflow, ReadingGuideConfig, ReadingGuideInput
# ── AII Conflict Detection Workflow (M-G1) ───────────────────────────────────
from omodul.conflict_detection_workflow import conflict_detection_workflow, ConflictDetectionConfig
