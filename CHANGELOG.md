# Changelog

<!-- Governance: see RELEASE_POLICY.md. main = release branch; feat branches deleted after merge; oprim → oskill → omodul merge order required; container bind-mount means git checkout is a live operation. -->

## [2.0.0] - 2026-05-25 — MAJOR

### Added — P6-B6 — Audience Data Workflow

- `omodul.audience_data_workflow` — Audience data collection + sentiment analysis pipeline.
  - 4 pillars: fingerprint, decision_trail, report, cost.
  - 6 stages: fetch_stats → fetch_comments → sentiment_analyze → feedback_extract → learnings → report.
  - `AudienceDataConfig` (platform/video_ids/analysis_depth/max_comments_per_video).
  - `AudienceDataInput` (oauth_token/cookies).
  - `AudienceDataFindings` (views/comments/sentiment/feedback/learnings).
  - `compute_fingerprint_for(config, input_data)` exposed.

---

## [2.0.0] - 2026-05-25 — MAJOR

### Changed — P6-B5 — generative_video_pipeline v2.0.0

**BREAKING**: Fingerprint algorithm changed (new fields in hash → all existing fingerprints invalidated).

- `_omodul_version` bumped to `"2.0.0"`.
- `_fingerprint_fields` expanded: `+image_to_video_enabled`, `+image_to_video_provider`, `+face_animation_provider`.
- Avatar assembly now uses `config.face_animation_provider` (was hardcoded `providers["avatar"]`).

### Added

- `GenerativeVideoConfig.image_to_video_enabled: bool = False` — Enable image→video animation stage.
- `GenerativeVideoConfig.image_to_video_provider: str = "wan22_local"` — Provider for i2v.
- `GenerativeVideoConfig.face_animation_provider: str = "wav2lip"` — Provider for face animation.
- `_stage_load_template(template_id)` — Load obase.template and inject system_prompt.
- `_stage_image_to_video(...)` — Animate frames via oskill.image_to_video_workflow.

### Backward Compatibility

- All new config fields have defaults → existing callers unaffected.
- Only fingerprint hash changes (MAJOR semver justification).

---

## [1.11.0] - 2026-05-24

### Changed — Phase 11B Wave 6 — TTS Deferral

- `AudioGeneratorAgent` — Returns `status=failed` with deferral message instead of attempting execution.
- `BUILTIN_JOB_SPECS` — Added `nightly_audio_gen` (disabled by default).

### Added — Hevi Batch 4 — Generative Video Pipeline

- `generative_video_pipeline(config, input_data, output_dir, on_step)` — End-to-end video generation pipeline with v0.8 §5.2 4-pillar support.
  - **fingerprint**: SHA-256 based on topic, main_line, providers, target_duration_s, language, template_id, portrait_path, bgm_path.
  - **decision_trail**: Per-stage step recording with layer/callable/inputs/outputs/timing.
  - **report**: Markdown 7-section report with custom findings serialization.
  - **cost**: CostTracker integration for LLM/image/TTS cost accumulation.
- `GenerativeVideoConfig(BaseConfig)` — Pipeline configuration with `_fingerprint_fields` whitelist.
- `GenerativeVideoInput` — Per-execution variable paths (portrait_path, bgm_path).
- `GenerativeVideoFindings` — Output model (video_path, duration, size, scenes/shots count).
- `compute_fingerprint_for(config, input_data)` — Public fingerprint function for service-layer deduplication.

---

## [1.10.0] - 2026-05-24

### Changed — Phase 11B Wave 6 — TTS Deferral

- `AudioGeneratorAgent` — Returns `status=failed` with deferral message instead of attempting execution.
- `BUILTIN_JOB_SPECS` — Added `nightly_audio_gen` (disabled by default).

### Added — BATCH 19 — High-level Platform Features

#### Deployment & Ops
- `appstore_deploy.py`: `appstore_deploy` — End-to-end AppStore template deployment. Supports catalog lookup, compose rendering, image pulling, and health wait.
- `cross_project_health_aggregate.py`: `cross_project_health_aggregate` — Aggregated health status across multiple projects based on Docker labels. Parallel health probing with project-level status matrix.

### Changed
- Version bump: 1.9.0 → 1.10.0
- Added `py.typed` for PEP 561 compliance.
- Fingerprint stability verified for both new moduls.

## [1.3.0] - 2026-05-20 — Sprint 0 (8 new omodul elements)

### Changed — Phase 11B Wave 6 — TTS Deferral

- `AudioGeneratorAgent` — Returns `status=failed` with deferral message instead of attempting execution.
- `BUILTIN_JOB_SPECS` — Added `nightly_audio_gen` (disabled by default).

### Added — Sprint 0 omodul elements
- `behavior.py`: `monthly_trade_review` — LLM-narrated monthly trade review with discipline scoring
- `behavior.py`: `training_task_recommend` — behavioral weakness → training task recommendation
- `strategy/daily_plan_generator.py`: `daily_plan_generate` — regime + theme + event driven daily plan
- `profile/individual_profile_workflow.py`: `individual_profile_workflow` — LLM security profile with cache/bust
- `simulation/paper_trading_session.py`: `paper_trading_session` — realistic paper trading (T+N, limit-up/down, commission, stamp tax)
- `backtest/user_system_backtest.py`: `user_system_backtest` — multi-year regime-conditional system backtest
- `signals.py`: `buy_sell_analysis` — LLM buy/sell analysis with BYOK routing and cache
- `strategy.py` (extension): `strategy_backtest_report` extended with `signal_detectors` + `regime_grouping` params

### Changed — Phase 11B Wave 6 — TTS Deferral

- `AudioGeneratorAgent` — Returns `status=failed` with deferral message instead of attempting execution.
- `BUILTIN_JOB_SPECS` — Added `nightly_audio_gen` (disabled by default).

### Added — JSON Schemas (14 files)
- `schemas/monthly_trade_review_{input,output}.schema.json`
- `schemas/training_task_recommend_{input,output}.schema.json`
- `schemas/daily_plan_{input,output}.schema.json`
- `schemas/individual_profile_{input,output}.schema.json`
- `schemas/paper_trading_session_{input,output}.schema.json`
- `schemas/user_system_backtest_{input,output}.schema.json`
- `schemas/buy_sell_analysis_{input,output}.schema.json`

### Fixed
- `strategies.py` (dead module): excluded from coverage — shadowed by `strategies/` package; coverage omit added
- `_base.py`, `_manifest.py`: added to coverage omit (trivial static configs)

### Changed
- Version bump: 1.2.2 → 1.3.0
- Coverage: 462 tests pass, 0 failures, 90.02% coverage ✓ (target ≥90%)

## [1.0.0] - 2026-05-14 — Generally Available (GA)

### Changed — Phase 11B Wave 6 — TTS Deferral

- `AudioGeneratorAgent` — Returns `status=failed` with deferral message instead of attempting execution.
- `BUILTIN_JOB_SPECS` — Added `nightly_audio_gen` (disabled by default).

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
### Changed — Phase 11B Wave 6 — TTS Deferral

- `AudioGeneratorAgent` — Returns `status=failed` with deferral message instead of attempting execution.
- `BUILTIN_JOB_SPECS` — Added `nightly_audio_gen` (disabled by default).

### Added — Phase 2 (13 elements)
- audit, data_norm, universe, alpha, portfolio, risk, execution, strategies modules

## [0.1.0] - 2026-05-10
### Changed — Phase 11B Wave 6 — TTS Deferral

- `AudioGeneratorAgent` — Returns `status=failed` with deferral message instead of attempting execution.
- `BUILTIN_JOB_SPECS` — Added `nightly_audio_gen` (disabled by default).

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
