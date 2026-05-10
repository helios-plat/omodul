# Changelog

## [0.1.0] - 2026-05-10

**Status: Candidate release. Dogfood validation pending before 1.0 promotion.**

**1.0 upgrade conditions:**
- ≥2 calling projects dogfood for ≥3 months
- Deferred modules (`standardized_performance_report`, `tradingview_signal_export`) decision finalized
- No breaking API changes needed based on dogfood feedback

### Added

#### Group 1: Trading Behavior
- `trade_journal_analyzer` — 4-bias behavioral diagnostics (disposition, overtrading, chasing, anchoring)
- `shadow_account_simulator` — Rule-based shadow account vs actual trades comparison

#### Group 2: Regime
- `regime_replay_search` — Historical analogue search + forward distribution projection
- `regime_change_detector` — Regime transition detection + before/after analysis
- `regime_conditional_dashboard_data` — Per-regime metrics + transition + pairwise shift

#### Group 3: Strategy Validation
- `strategy_backtest_report` — Complete backtest report (PSR/DSR + Bootstrap Sharpe + CPCV + WFO + regime + factors)
- `strategy_decay_monitor` — 4-state decay machine (HEALTHY/DEGRADING/CRITICAL/DEAD)
- `factor_attribution_report` — Multi-model factor attribution comparison

#### Group 4: Signal & Alert
- `alert_calibration_engine` — Alert calibration + Bandit feedback loop
- `thesis_invalidation_monitor` — Thesis validity 4-state monitor

#### Group 5: Risk
- `scenario_stress_test` — Historical + custom + analogy stress scenarios
- `tail_risk_analyzer` — Multi-method VaR/ES + tail metrics + normality test

#### Group 6: Data Quality
- `panel_data_quality_check` — Gap + outlier + freshness + drift weighted score
- `cross_source_consistency_check` — Multi-source pairwise correlation + shift + recommendation

#### Group 7: Similarity
- `smart_peer_finder` — Multi-dimensional similarity ensemble peer search
- `event_cascade_clusterer` — DBSCAN event clustering with time window + outlier detection

### Infrastructure
- Real data fixtures (SPY 252d, BTC 1y panel, FF5 factors 5y)
- 78 tests, 90% coverage
- Layer 3 discipline enforcement (no internal imports, must use oprim/oskill)
