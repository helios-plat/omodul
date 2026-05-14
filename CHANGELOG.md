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

---

## Release Governance Note (2026-05-14)

During the Phase 10 release process, we discovered that Phases 4-10 of oprim and
oskill had been accumulated on a single long-running feature branch
(feat/v1.7.0-phase4) without intermediate merges to main.

**omodul note**: Unlike oprim/oskill, omodul main was kept relatively current.
However, intermediate development versions 0.7 / 0.8 / 0.9 were developed but
not separately tagged. All work is consolidated into v1.0.0 GA release. This is
the documented historical record; no retroactive tagging is performed.

**Resolution**: fast-forward merged main to v1.0.0, pushed 5 local commits to
origin. See `RELEASE_POLICY.md` for the corrected workflow.

All future Phase releases must:
1. Use independent feat branches (not accumulate Phases on one branch)
2. Merge to main via PR before tagging
3. Tag on main (never on feat branches)
