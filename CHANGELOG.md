# Changelog

<!-- Governance: see RELEASE_POLICY.md. main = release branch; feat branches deleted after merge; oprim → oskill → omodul merge order required; container bind-mount means git checkout is a live operation. -->

## [1.26.0] — 2026-06-13

### Added (hevi v2 — M8)
- feat: `agentic_longvideo_pipeline` — 4 时长档 (1-5min/5-15min/15-45min/45min+), 6 阶段编排 (脚本→分镜→参考选取→视频→一致性校验→音频/字幕/合成), select_reference + mllm_frame_consistency_check 集成, 重试/fallback 提供商
- feat: `LongVideoConfig`, `LongVideoResult` Pydantic 模型
- deprecate: `generative_video_pipeline` 标记 DeprecationWarning (下一 MAJOR 删除)

## [1.25.1] — 2026-06-13

### Fixed
- `appstore_deploy`: `from oprim import compose_up` → `from obase.docker import compose_up` (跟上 obase 迁移)

## [1.25.0] — 2026-06-12

### Changed
- batch-3: `node_register` / `autoheal_cycle` / `watch_cycle` 迁 `obase.docker` + `obase.persistence`, 全部改 async

## [1.24.0] — 2026-06-12

### Changed
- batch-2: `rollback_app` / `install_self_hosted_app` / `upgrade_self_hosted_app` 等 6 个 docker omodul 迁 `obase.docker`

## [1.23.0] — 2026-06-12

### Changed
- batch-1: `export_user_data_csv` / `sync_user_preferences` / `verify_email_workflow` / `reset_password_workflow` 迁 `obase.persistence`, 全部改 async

## [1.22.1] — 2026-06-12

### Fixed
- fix: 修正 _version.py / pyproject.toml 版本不一致（"chore: bump to 1.22.0" 漏更新 _version.py，实际为 1.21.0）; 无 paramiko 直接用法（oprim 已声明，transitive 覆盖）

## [1.21.0] — 2026-06-05

### Added — Aegis 3O Batch 4 (3 new omodul elements)
- feat: `diagnose_queue_health` — RabbitMQ 队列健康诊断 (oprim.rabbitmq_queue_depth/consumer_count → oskill pattern/severity/circuit_breaker; B option: needs_deep_investigation flag 代替调用 oservice)
- feat: `diagnose_connection_pool` — PostgreSQL 连接池诊断 (oprim.postgres_long_running_queries/locks_status → oskill classify/pattern/severity; B option: needs_deep_investigation flag)
- feat: `diagnose_service_health` — 服务综合健康诊断 (oprim.network_http_health/docker_inspect/system_cpu/ram → oskill classify/pattern/severity; B option: needs_deep_investigation flag; container_name 可选)
- All 3: BaseConfig subclass, standard signature, 7-key return dict, status="failed" no-raise, decision_trail.json in finally
- test: 39 新测试 (test_aegis_b4.py: 13+12+14)
- Note: generate_incident_postmortem already shipped in v1.17.0; no change needed for B4

## [1.20.0] — 2026-06-05

### Changed
- `graphrag_query`: 取 all_nodes 改走 `backend.list_nodes()` 优先 (Protocol 公开接口), `_nodes` 私有属性降级为 InMemoryBackend legacy fallback; result dict 含新字段 `knowledge_type`
- `knowledge_reflux._graph_snapshot`: 同样改走 `backend.list_nodes()` + `list_edges()` 优先; `_nodes`/`_edges` (InMemory legacy) + `_conn` (SqlBackend legacy) 保留作降级路径
- 向后兼容: InMemoryBackend / SqlBackend 旧路径保留, 不破坏现有调用方

### Fixed
- 不再从外部伸手进 `backend._nodes` / `backend._conn` 私有属性 (与陷阱 11 同模式治理 — 依赖他对象私有接口)

### Tests
- 新增 `test_protocol_backend_list_nodes_path` + `test_protocol_backend_knowledge_type_in_result` (graphrag_query 公开路径覆盖)
- 新增 `test_protocol_backend_list_nodes_path` + `test_protocol_backend_dangling_ref_detected` (knowledge_reflux 公开路径覆盖)

### Sweep
- oprim / oskill / obase / oservice 全局扫描: 无跨对象私有属性访问违规 (oprim.MetaDB._conn + obase.dns_pinned_transport._context 均为自有属性, 不属于此模式)

> 本 release 由 AII 经理人发现 + 提交修法 + AII 侧验证, Owner 采纳归入主库.
> AII CC 越界改主库工作区是特例, 已立永久禁令, 不形成先例.

## [1.19.1] — 2026-06-05

### Fixed
- `process_inbox_substrate`: pass `user_id_hash=config.user_id_hash` as direct arg to `ingest_substrate` (oskill v3.13.2 required param)
- `knowledge/process_inbox`: add `user_id_hash: str` to `process_inbox()` signature; propagate to `ingest_substrate` call
- `knowledge/browser_extension/server`: add `user_id_hash` param to `_run_ingest`; `ingest_page` reads `STRATUM_USER_ID` from oprim config and forwards it
- `knowledge/agents/builtin/knowledge_curator`: pass `user_id_hash=context.user_id` to `ingest_substrate`
- Tests: update all `process_inbox()` call sites with `user_id_hash="test_user"`; update `_run_ingest` direct calls and mock signatures in browser extension tests; add `test_process_inbox_substrate_passes_user_id_hash`

## [1.19.0] — 2026-06-04

### Added (AII-3O Batch 5b — P5 verification + learning + governance)
- `verify_knowledge` / `VerifyKnowledgeConfig` — A20 defeasible grade upgrade/downgrade via cmi_verify (causal), backtest_stat (quantitative), or manual verdict; grade capped at "high" from auto-verify
- `learning_distill` / `LearningDistillConfig` — distill Episode → solution_strategy KU via llm_distill_strategy + ku_gate_validate gate; A19 unverified by default; quarantine path on invalid KU
- `governance_adjudicate` / `GovernanceAdjudicateConfig` — L0-L4 tiered adjudication: L0 auto-approved, L1-L2 needs_review, L3-L4 escalate; coherence_compute + ku_gate_validate evidence validation

## [1.18.0] — 2026-06-04

### Added (AII-3O Batch 5a — P4 GraphRAG)
- `graphrag_query` / `GraphRAGQueryConfig` — GraphRAG-style knowledge retrieval combining oprim.vector_encode (semantic similarity) + oprim.entity_graph_search (graph expansion) + epistemic grade filtering; decision_trail pillar

## [1.17.0] — 2026-06-04

### Added (AII-3O Batch 4b)
- `register_ku` / `RegisterKuConfig` — register a KU per HOS-001 three-face schema; fingerprint + decision_trail pillars
- `store_memory` / `StoreMemoryConfig` — store query/case/solution_strategy memory as KUs; fingerprint + decision_trail pillars
- `reuse_strategy` / `ReuseStrategyConfig` — k=1 similarity match to stored strategies + A22 epistemic grade check; decision_trail pillar

## [1.16.0] — 2026-06-04

### Added (AII-3O Batch 2 — omodul migration)
- `register_entity` / `RegisterEntityConfig` — entity registration with fingerprint + decision_trail
- `append_episode` / `AppendEpisodeConfig` — episodic memory entry with orphan guard
- `knowledge_reflux` / `KnowledgeRefluxConfig` (`run_reflux`) — deterministic knowledge graph completeness check (dangling refs, contradictions, inverse relations, supersede propagation, required fields)
- `cognitive_diagnosis` / `CognitiveDiagnosisConfig` (`run_diagnosis`) — DINA + EM diagnostic core (ADR-A23 compliant: descriptive+diagnostic only, no psychological labels)

## [1.15.1] — 2026-06-04

### Added
- `BaseConfig` exported from `omodul` top-level (was defined in `_base_config.py` but not exported)

### Fixed
- Downstream consumers can now `from omodul import BaseConfig` without internal imports

## [1.15.0] - 2026-06-03 — feat: illustration_agent builtin (Stratum v0.6 §17.1)

### Added

- `omodul/knowledge/agents/builtin/illustration_agent.py` — `IllustrationAgent` builtin agent: substrate → 1-3 illustration derivatives via `oprim.image_generate` (wanxiang provider). Steps: fetch/generate substrate summary → LLM image prompt → `oprim.image_generate` → write derivative record. Mirrors `audio_generator` pattern.
- `omodul/__init__.py`: top-level re-export of `IllustrationAgent` (避免 export 漏症)
- 13 tests, all passing

## [1.14.2] - 2026-06-02 — fix: compute_commission/compute_stamp_tax ghost names + full oprim call audit

### Fixed

- `omodul/simulation/paper_trading_session.py`: renamed 2 ghost oprim calls + converted to keyword-only
  - `oprim.commission(pos, pos, pos)` → `oprim.compute_commission(trade_amount=..., rate=..., min_fee=...)` (name not in oprim __all__ + `*`-enforced)
  - `oprim.stamp_tax(pos, pos, pos)` → `oprim.compute_stamp_tax(trade_amount=..., rate=..., direction=...)` (name not in oprim __all__ + `*`-enforced)
- Full audit: 0 additional ghost names; 0 other `*`-enforced functions called positionally

## [1.14.1] - 2026-06-02 — fix: positional → keyword args for oprim keyword-only functions

### Fixed

- `omodul/simulation/paper_trading_session.py`: converted 3 positional calls to keyword-only
  - `oprim.detect_daily_limit_up(close_price=..., prev_close=..., limit_pct=...)` (oprim 2.22.0 keyword-only)
  - `oprim.detect_daily_limit_down(close_price=..., prev_close=..., limit_pct=...)` (oprim 2.22.0 keyword-only)
  - `oprim.t_plus_n_blocked(entry_date=..., current_date=..., t_plus_n=...)` (oprim 2.22.0 keyword-only)

## [1.14.0] - 2026-06-01 — Stratum Batch 3: 8 omodul (P0 + P1 subset)

### Added — Stratum B3

**P0 core workflows (5)**
- `process_inbox_substrate` — File → parse → classify → ingest → derive; fullest pillars (fp+trail+report)
- `daily_digest_workflow` — hybrid_search → llm_summarize → generate_derivative digest note
- `send_welcome_email` — Welcome email via template_render + push_email; fingerprint only
- `reset_password_workflow` — Secure token + DB write + email; decision_trail only (security audit)
- `verify_email_workflow` — Two-phase OTP send/verify; fingerprint only

**P1 light workflows (3)**
- `notification_dispatch_workflow` — Multi-channel notification via template_render + push_email
- `export_user_data_csv` — db_query → csv_writer; fingerprint + report
- `sync_user_preferences` — db_read → resolve_conflict (oskill) → db_write; fingerprint

### Notes
- All 8 omodul: standard signature (config, input_data, output_dir) → dict
- All 8: never raise (status="failed" on error)
- All 8: _enabled_pillars explicitly declared
- H1-modul compliant: no sibling omodul calls
- P1 remaining (15 omodul) + P2 (7 omodul) deferred to v1.15.0
- 65 new tests total (24 + 19 + 22)

---

## [1.13.2] - 2026-05-31 — fix: top-level re-export for 4 elements

### Fixed

- `omodul.__init__` re-export `alert_calibration_engine` (from `omodul.signals`)
- `omodul.__init__` re-export `training_task_recommend` (from `omodul.behavior`)
- `omodul.__init__` re-export `user_system_backtest` (from `omodul.backtest.user_system_backtest`)
- `omodul.__init__` re-export `panel_data_quality_check` (from `omodul.data_quality`)
- `__all__`補完: v1.13.1 の5要素 + v1.13.2 の4要素を `__all__` に追加

## [1.13.1] - 2026-05-31 — fix: top-level re-export for 5 elements

### Fixed

- `omodul.__init__` re-export `monthly_trade_review` (was ImportError in conftest — S4-2 blocker)
- `omodul.__init__` re-export `paper_trading_session`, `individual_profile_workflow`, `buy_sell_analysis`, `daily_plan_generate`
- `pyproject.toml` version synced to `_version.py` (was stale at 1.12.0)

## [1.13.0] - 2026-05-30 — Tide v4 step2 B11 (5 omoduls)

### Added — Tide v4 B11 — 宏观日报/龙虎榜/计划卡片/纪律看板/月度复盘

- `omodul.macro_daily_report` — 宏观日报生成. Pillars: fingerprint+decision_trail+report+cost. 组合: macro_surprise_compute+macro_cycle_engine_v2+policy_sector_attribution+LLM. ThreadPoolExecutor 并发 async B10 oskill 调用.
- `omodul.lhb_institution_vs_hotmoney_panel` — 龙虎榜机构 vs 游资面板. Pillars: fingerprint+decision_trail. 组合: seat_winrate_aggregator+unknown_seats_audit_loop+fetch_sector_returns.
- `omodul.plan_card_render` — 计划卡片渲染. Pillars: fingerprint+decision_trail. 组合: candidate_universe_builder_v3+similar_context_injector. ProviderRegistry LLM 注入.
- `omodul.discipline_banner_toast_data` — 纪律看板/Toast 数据. Pillars: fingerprint+decision_trail. 组合: discipline_vs_violation_winrate_compute+stop_loss_compliance_check. banner_severity 三级判断.
- `omodul.monthly_review_cron_orchestrator` — 月度复盘 Cron 编排. Pillars: fingerprint+decision_trail+report+cost. 组合: discipline_vs_violation_winrate_compute+monthly_review_jinja2_render+LLM.
- 68 tests / 0 B11 mypy errors / 5 compute_fingerprint_for公开 API + 路由注册.

## [1.12.0] - 2026-05-28 — Tide v4 B3-B5 extraction (3 omoduls)

### Added — Tide v4 B3-B5 — A股评分/Regime/候选池 omodul

- `omodul.symbol_dim_score` — 8 维度综合评分. Pillars: fingerprint + decision_trail. ThreadPoolExecutor 并行 8 dim. IO-free.
- `omodul.regime_inference` — Regime 推断. multi_state_classify → regime_smoothing.
- `omodul.candidate_pool` — 候选池构建. H1-compliant. apply_screen_filter + regime_conditional_score_weighted.

DEVIATION §2.3: ThreadPoolExecutor without manual copy_context() — Python 3.12+ auto-propagates contextvars. Cost pillar not enabled. Awaiting Owner confirmation.

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

## [1.26.1] — 2026-06-13
### Fixed
- apply_changeset: 补充 compute_fingerprint_for 函数定义（whl拆分时遗漏导致 NameError）

## [1.26.2] — 2026-06-13
### Fixed
- candidate_pool / symbol_dim_score: 修正 apply_screen_filter import 路径
  (from oprim.apply_screen_filter → from oprim import apply_screen_filter)
  oprim v3.6.x 惰性化后子模块直接 import 路径失效，改顶层 import
