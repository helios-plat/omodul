"""omodul — End-to-end financial analysis features built on oprim + oskill."""

from omodul._version import __version__
from omodul.alpha_signals import bocpd_trend, funding_rate_directional, ofi_meanrev
from omodul.asset_pricing.ez_workflow import epstein_zin_asset_pricing_workflow
from omodul.audit import vcp_silver_record
# behavior imports consolidated below (Sprint 0)

# Phase 10: New workflow elements
from omodul.behavioral.portfolio_workflow import behavioral_portfolio_workflow
from omodul.data_normalization import okx_to_nautilus
from omodul.data_quality import cross_source_consistency_check, panel_data_quality_check
from omodul.execution_models import aggressive_limit, twap_with_impact
from omodul.microstructure.state_mm_strategy import state_dependent_market_making_strategy
from omodul.portfolio import execution_cost_model, kelly_allocator, risk_parity
from omodul.portfolio.high_dim_workflow import high_dim_portfolio_workflow
from omodul.portfolio_construction import vol_target
from omodul.regime import (
    regime_change_detector,
    regime_conditional_dashboard_data,
    regime_replay_search,
)
from omodul.reporting.cross_framework_benchmark import cross_framework_benchmark_report
from omodul.risk import scenario_stress_test, tail_risk_analyzer
from omodul.risk.systemic_dashboard import systemic_risk_dashboard
from omodul.risk_models import drawdown_circuit_breaker
from omodul.robust.decision_workflow import robust_decision_workflow
from omodul.signals import alert_calibration_engine, buy_sell_analysis, thesis_invalidation_monitor
from omodul.similarity import event_cascade_clusterer, smart_peer_finder
from omodul.strategies import bocpd_trend_following, funding_rate_arbitrage, microstructure_scalper
from omodul.strategy import (
    daily_plan_generate,
    factor_attribution_report,
    strategy_backtest_report,
    strategy_decay_monitor,
)
from omodul.simulation.paper_trading_session import paper_trading_session
from omodul.backtest.user_system_backtest import user_system_backtest
from omodul.profile.individual_profile_workflow import individual_profile_workflow
from omodul.behavior import (
    monthly_trade_review,
    shadow_account_simulator,
    trade_journal_analyzer,
    training_task_recommend,
)
from omodul.llm_workflows import multi_agent_consensus
from omodul.universe_selection import fixed_list

__all__ = [
    "__version__",
    # Group 1: Trading Behavior
    "trade_journal_analyzer",
    "shadow_account_simulator",
    # Group 2: Regime
    "regime_replay_search",
    "regime_change_detector",
    "regime_conditional_dashboard_data",
    # Group 3: Strategy
    "strategy_backtest_report",
    "strategy_decay_monitor",
    "factor_attribution_report",
    # Group 4: Signals
    "alert_calibration_engine",
    "thesis_invalidation_monitor",
    # Group 5: Risk
    "scenario_stress_test",
    "tail_risk_analyzer",
    # Group 6: Data Quality
    "panel_data_quality_check",
    "cross_source_consistency_check",
    # Group 7: Similarity
    "smart_peer_finder",
    "event_cascade_clusterer",
    # Group 8: Portfolio (NEW from Selene)
    "kelly_allocator",
    "risk_parity",
    "execution_cost_model",
    # Group 9: VCP Audit
    "vcp_silver_record",
    # Group 10: Data Normalization
    "okx_to_nautilus",
    # Group 11: Universe Selection
    "fixed_list",
    # Group 12: Alpha Signals
    "bocpd_trend",
    "ofi_meanrev",
    "funding_rate_directional",
    # Group 13: Portfolio Construction
    "vol_target",
    # Group 14: Risk Models
    "drawdown_circuit_breaker",
    # Group 15: Execution Models
    "twap_with_impact",
    "aggressive_limit",
    # Group 16: Strategies
    "bocpd_trend_following",
    "microstructure_scalper",
    "funding_rate_arbitrage",
    # Group 18: Behavioral Portfolio (Phase 10)
    "behavioral_portfolio_workflow",
    # Group 19: Systemic Risk (Phase 10)
    "systemic_risk_dashboard",
    # Group 20: High-Dim Portfolio (Phase 10)
    "high_dim_portfolio_workflow",
    # Group 21: Robust Decision (Phase 10)
    "robust_decision_workflow",
    # Group 22: State MM (Phase 10)
    "state_dependent_market_making_strategy",
    # Group 23: Asset Pricing (Phase 10)
    "epstein_zin_asset_pricing_workflow",
    # Group 24: Reporting (Phase 10)
    "cross_framework_benchmark_report",
    # Group 25: LLM Workflows (Phase 3 P15)
    "multi_agent_consensus",
    # Sprint 0: New omodul elements
    "monthly_trade_review",
    "training_task_recommend",
    "daily_plan_generate",
    "individual_profile_workflow",
    "paper_trading_session",
    "user_system_backtest",
    "buy_sell_analysis",
]
