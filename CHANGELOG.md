# Changelog

<!-- Governance: see RELEASE_POLICY.md. main = release branch; feat branches deleted after merge; oprim → oskill → omodul merge order required; container bind-mount means git checkout is a live operation. -->

## [1.0.0] - 2026-05-14 — Generally Available (GA)

### Added — Phase 10 (7 new workflow elements)
- `behavioral/portfolio_workflow.py`: `behavioral_portfolio_workflow` — CPT + ambiguity aversion decision pipeline
- `risk/systemic_dashboard.py`: `systemic_risk_dashboard` — CoVaR/MES/SRISK/Eisenberg-Noe reporting
- `portfolio/high_dim_workflow.py`: `high_dim_portfolio_workflow` — HRP-v2 + SSD-MILP allocation
- `robust/decision_workflow.py`: `robust_decision_workflow` — multiplier/KMM/variational control
- `microstructure/state_mm_strategy.py`: `state_dependent_market_making_strategy` — Hawkes state-driven MM
- `asset_pricing/ez_workflow.py`: `epstein_zin_asset_pricing_workflow` — Epstein-Zin utility chain
- `reporting/cross_framework_benchmark.py`: `cross_framework_benchmark_report` — cross-model performance

### Fixed
- `audit.py`: `sha256_hash` returns str (not bytes) — added `bytes.fromhex()` conversion; `hash_prev` now accepts str/bytes/None
- `strategies.py`: ORANGE risk gate now triggers early-return (same as RED)
- `strategies.py`: `_args_hash` `.hex()` call fixed for str-returning `sha256_hash`
- `strategies.py`: `bocpd`/`basis_decomposition`/`order_flow_imbalance` fallback functions now properly bound when imports fail

### Changed
- Version bump: 0.2.0 → 1.0.0 (GA release, API frozen)
- Dependency: oprim >=2.0.0,<3.0.0, oskill >=2.0.0,<3.0.0

## [0.2.0] - 2026-05-12
### Added — Phase 2 (13 elements)
- audit, data_norm, universe, alpha, portfolio, risk, execution, strategies modules

## [0.1.0] - 2026-05-10
### Added — Initial release (3 elements: kelly_allocator, risk_parity, execution_cost_model)
