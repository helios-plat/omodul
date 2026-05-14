"""omodul — End-to-end financial analysis features built on oprim + oskill."""

from omodul._version import __version__
from omodul.behavior import shadow_account_simulator, trade_journal_analyzer
from omodul.data_quality import cross_source_consistency_check, panel_data_quality_check
from omodul.portfolio import execution_cost_model, kelly_allocator, risk_parity
from omodul.regime import (
    regime_change_detector,
    regime_conditional_dashboard_data,
    regime_replay_search,
)
from omodul.risk import scenario_stress_test, tail_risk_analyzer
from omodul.signals import alert_calibration_engine, thesis_invalidation_monitor
from omodul.similarity import event_cascade_clusterer, smart_peer_finder
from omodul.strategy import (
    factor_attribution_report,
    strategy_backtest_report,
    strategy_decay_monitor,
)
from omodul.audit import vcp_silver_record
from omodul.data_normalization import okx_to_nautilus
from omodul.universe_selection import fixed_list
from omodul.alpha_signals import bocpd_trend, ofi_meanrev, funding_rate_directional
from omodul.portfolio_construction import vol_target
from omodul.risk_models import drawdown_circuit_breaker
from omodul.execution_models import twap_with_impact, aggressive_limit
from omodul.strategies import bocpd_trend_following, microstructure_scalper, funding_rate_arbitrage

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
]
