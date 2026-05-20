"""omodul manifest — element registry."""
from __future__ import annotations

VERSION = "1.3.0"

ELEMENTS = {
    # Phase 1-4: Core business workflows
    "trade_journal_analyzer": "omodul.behavior",
    "shadow_account_simulator": "omodul.behavior",
    "regime_replay_search": "omodul.regime",
    "regime_change_detector": "omodul.regime",
    "regime_conditional_dashboard_data": "omodul.regime",
    "strategy_backtest_report": "omodul.strategy",
    "strategy_decay_monitor": "omodul.strategy",
    "factor_attribution_report": "omodul.strategy",
    "alert_calibration_engine": "omodul.signals",
    "thesis_invalidation_monitor": "omodul.signals",
    "scenario_stress_test": "omodul.risk",
    "tail_risk_analyzer": "omodul.risk",
    "panel_data_quality_check": "omodul.data_quality",
    "cross_source_consistency_check": "omodul.data_quality",
    "smart_peer_finder": "omodul.similarity",
    "event_cascade_clusterer": "omodul.similarity",
    # Phase 5: Kelly/Risk Parity/Execution Cost
    "kelly_allocator": "omodul.portfolio",
    "risk_parity": "omodul.portfolio",
    "execution_cost_model": "omodul.portfolio",
    # Phase 5D: Alpha Signals
    "bocpd_trend": "omodul.alpha_signals",
    "ofi_meanrev": "omodul.alpha_signals",
    "funding_rate_directional": "omodul.alpha_signals",
    # Phase 5E: Portfolio Construction + Risk Models + Execution
    "vol_target": "omodul.portfolio_construction",
    "drawdown_circuit_breaker": "omodul.risk_models",
    "twap_with_impact": "omodul.execution_models",
    "aggressive_limit": "omodul.execution_models",
    # Phase 5E: Strategies
    "bocpd_trend_following": "omodul.strategies",
    "microstructure_scalper": "omodul.strategies",
    "funding_rate_arbitrage": "omodul.strategies",
    # Phase 5-6: Audit
    "vcp_silver_record": "omodul.audit",
    "okx_to_nautilus": "omodul.data_normalization",
    "fixed_list": "omodul.universe_selection",
    # Phase 6E-6F: Bayesian Analysis
    "estimate_alpha_posterior": "omodul.bayesian_analysis.alpha_estimation",
    "compare_strategies_bayesian": "omodul.bayesian_analysis.strategy_comparison",
    "combine_alpha_posteriors": "omodul.bayesian_analysis.posterior_combiner",
    # Phase 6E: Validation
    "backtest_overfitting_report": "omodul.advanced_validation.overfitting",
    "haircut_sharpe_report": "omodul.advanced_validation.haircut",
    # Phase 6F: Statistical Modeling
    "pairs_trading_workflow": "omodul.statistical_modeling.pairs_trading_workflow",
    "regime_volatility_report": "omodul.statistical_modeling.regime_volatility_report",
    "stationarity_diagnostics": "omodul.statistical_modeling.stationarity_diagnostics",
    "autocorrelation_diagnostics": "omodul.statistical_modeling.autocorrelation_diagnostics",
    # Phase 6F: Factor Analysis
    "factor_attribution_workflow": "omodul.factor_analysis.factor_attribution_report",
    "factor_combination": "omodul.factor_analysis.factor_combination",
    "factor_diagnostics": "omodul.factor_analysis.factor_diagnostics",
    # Phase 7F: Conformal / Distributional RL / Causal / Synthetic
    "conformal_signal_uncertainty": "omodul.conformal.signal_uncertainty",
    "distributional_rl_strategy": "omodul.distributional_strategies.qr_strategy",
    "causal_factor_attribution": "omodul.causal_analysis.factor_attribution",
    "synthetic_backtest_augmentation": "omodul.synthetic_augmentation.backtest_augmentation",
    # Phase 10F: Behavioral / Systemic / High-Dim / Robust / MM / EZ / Reporting
    "behavioral_portfolio_workflow": "omodul.behavioral.portfolio_workflow",
    "systemic_risk_dashboard": "omodul.risk.systemic_dashboard",
    "high_dim_portfolio_workflow": "omodul.portfolio.high_dim_workflow",
    "robust_decision_workflow": "omodul.robust.decision_workflow",
    "state_dependent_market_making_strategy": "omodul.microstructure.state_mm_strategy",
    "epstein_zin_asset_pricing_workflow": "omodul.asset_pricing.ez_workflow",
    "cross_framework_benchmark_report": "omodul.reporting.cross_framework_benchmark",
    # Sprint 0: New elements
    "monthly_trade_review": "omodul.behavior",
    "training_task_recommend": "omodul.behavior",
    "daily_plan_generate": "omodul.strategy.daily_plan_generator",
    "individual_profile_workflow": "omodul.profile.individual_profile_workflow",
    "paper_trading_session": "omodul.simulation.paper_trading_session",
    "user_system_backtest": "omodul.backtest.user_system_backtest",
    "buy_sell_analysis": "omodul.signals",
}

CATEGORIES = {
    "behavior": ["trade_journal_analyzer", "shadow_account_simulator", "monthly_trade_review", "training_task_recommend"],
    "regime": ["regime_replay_search", "regime_change_detector", "regime_conditional_dashboard_data"],
    "strategy": ["strategy_backtest_report", "strategy_decay_monitor", "factor_attribution_report"],
    "signals": ["alert_calibration_engine", "thesis_invalidation_monitor", "buy_sell_analysis"],
    "risk": ["scenario_stress_test", "tail_risk_analyzer", "drawdown_circuit_breaker"],
    "data_quality": ["panel_data_quality_check", "cross_source_consistency_check"],
    "similarity": ["smart_peer_finder", "event_cascade_clusterer"],
    "portfolio": ["kelly_allocator", "risk_parity", "execution_cost_model",
                  "vol_target", "high_dim_portfolio_workflow"],
    "alpha_signals": ["bocpd_trend", "ofi_meanrev", "funding_rate_directional"],
    "execution": ["twap_with_impact", "aggressive_limit"],
    "strategies": ["bocpd_trend_following", "microstructure_scalper", "funding_rate_arbitrage"],
    "audit": ["vcp_silver_record"],
    "data_normalization": ["okx_to_nautilus"],
    "universe_selection": ["fixed_list"],
    "bayesian_analysis": ["estimate_alpha_posterior", "compare_strategies_bayesian", "combine_alpha_posteriors"],
    "validation": ["backtest_overfitting_report", "haircut_sharpe_report"],
    "statistical_modeling": ["pairs_trading_workflow", "regime_volatility_report",
                             "stationarity_diagnostics", "autocorrelation_diagnostics"],
    "factor_analysis": ["factor_attribution_workflow", "factor_combination", "factor_diagnostics"],
    "conformal": ["conformal_signal_uncertainty"],
    "distributional_rl": ["distributional_rl_strategy"],
    "causal_analysis": ["causal_factor_attribution"],
    "synthetic_augmentation": ["synthetic_backtest_augmentation"],
    "behavioral": ["behavioral_portfolio_workflow"],
    "systemic_risk": ["systemic_risk_dashboard"],
    "robust": ["robust_decision_workflow"],
    "microstructure": ["state_dependent_market_making_strategy"],
    "asset_pricing": ["epstein_zin_asset_pricing_workflow"],
    "reporting": ["cross_framework_benchmark_report"],
    "profile": ["individual_profile_workflow"],
    "simulation": ["paper_trading_session"],
    "backtest_user": ["user_system_backtest"],
    "daily_plan": ["daily_plan_generate"],
}
