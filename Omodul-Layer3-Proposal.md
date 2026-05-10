# omodul Layer 3 候选清单提案

**版本**: v1.0 (预判候选)  
**日期**: 2026-05-09  
**状态**: PRELIMINARY (待 4 项目 evaluation extraction 验证)  
**依赖**: oprim 1.0 + oskill 1.0 已完工  
**前置文档**: ADR-061 (Layer 1) + ADR-062 (Layer 2) + Helios-Engineering-Stack-Methodology.md

---

## 0. 这份文档是什么

**用途**：基于 22 章 Spec + 三层栈方法论，**预判** Layer 3 omodul 的候选清单。

**警告**：

```
⚠ 这是预判, 不是最终清单
⚠ 必须经过 4 项目 evaluation extraction 阶段验证
⚠ 候选数量 / 命名 / 边界都可能调整
⚠ 作为 Layer 3 ADR-063 的对照基线, 不是 spec
```

**不是什么**：
- ❌ 不是每个 feature 的完整 spec（那是 ADR-063 的事）
- ❌ 不是 ship-ready prompt（必须等 evaluation 验证后再写）
- ❌ 不是优先级排序（按你方法论"都是必须的"原则）

**是什么**：
- ✅ 候选清单（含跨项目用例 + 调用 ops/skills 预判）
- ✅ 每个候选的 confidence 标注
- ✅ 待 evaluation 验证的疑问点

---

## 1. Layer 3 严格定义（再次校准）

```yaml
layer_3_definition:

  inclusion (必须全部满足):
    ✅ 端到端可用业务功能模块 (有清晰输入 → 有可用输出)
    ✅ 跨 ≥ 2 项目可复用 (中性, 不绑特定项目)
    ✅ 仅依赖 Layer 1 (oprim) + Layer 2 (oskill) + 标准库
    ✅ 不调用其它 Layer 3 element
    ✅ 输入用通用 schema (DataFrame / dict, 不绑特定项目存储)
    ✅ 输出用通用结果 (dict / DataFrame, 不绑特定项目展示)

  exclusion (任一即排除):
    ❌ 单项目专用 → 业务模块层
    ❌ 必须依赖项目特定 schema → 抽象不到位
    ❌ 输出绑特定 UI 格式 → 业务模块
    ❌ 调用其它 Layer 3 → 退化
    ❌ 业务概念绑定 (e.g. Helios "Fusion Score") → 业务模块

  positive_examples:
    omodul.trade_journal_analyzer
    omodul.regime_replay_search
    omodul.factor_attribution_report
    
  excluded_as_business_module:
    helios_fusion_decision_board    # 绑 Helios UI 业务
    helixa_strategy_state_machine   # 绑 Helixa 4-state lifecycle
    tide_a_share_panel              # 绑 Tide A 股 UI
```

---

## 2. 候选清单 (16 个)

按业务领域分组，**组内无优先级**，**组间无优先级**。

每个候选标注：
- **confidence**: 强（Spec 多次提及 + ≥ 2 项目明确用）/ 中（跨项目预判）/ 弱（待评估）
- **调用的 skills/ops**
- **跨项目用例**
- **关键疑问** (待 evaluation 验证)

---

### 组 1：交易行为分析（Trade Behavior Analysis）—— 2 个

#### 1.1 `features.trade_journal_analyzer` ⭐ 强候选

- **用途**：交易记录 → 4 类 bias 诊断 + 行为模式分析
- **4 类 bias**:
  - Disposition effect（处置效应：过早卖盈利、太晚割亏损）
  - Overtrading（过度交易）
  - Chasing momentum（追涨杀跌）
  - Anchoring（锚定效应）
- **调用 skills/ops**:
  - `skills.detect_outliers_robust`（异常交易检测）
  - `skills.distribution_shift_test`（行为模式漂移）
  - `skills.bootstrap_distribution`（统计量 CI）
  - `ops.percentile_rank`、`ops.zscore_normalize`
- **API（预判）**:
  ```python
  def trade_journal_analyzer(
      trades: pd.DataFrame,  # 通用 schema: ts, symbol, side, qty, price, ...
      *,
      diagnostics: list = ["disposition", "overtrading", "chasing", "anchoring"],
      benchmark_returns: pd.Series | None = None,
  ) -> dict:
      # returns: {bias_diagnostics, behavior_metrics, summary_report}
  ```
- **跨项目用例**:
  - Helios（如有"我的交易"功能）
  - Helixa（自我诊断 paper trading 行为）
  - Tide（A 股版同样功能）
  - 任何含交易记录的项目
- **来源**: 受 Vibe-Trading "Trade Journal Analyzer" 启发（你今天对话提到）
- **关键疑问**:
  - Helios Spec 22 章中此 feature 不强（仅 §13 持仓有部分）
  - 是否真"≥ 2 项目用"还是 Helixa-only？→ **待 evaluation 验证**

---

#### 1.2 `features.shadow_account_simulator` 中候选

- **用途**：基于规则的"影子账户"模拟（"如果你严格按规则交易会怎样"）
- **核心逻辑**:
  - 输入：实际交易历史 + 规则集
  - 输出：影子账户的 P&L + 与实际差异 + 偏离规则的次数
- **调用 skills/ops**:
  - `skills.regime_aware_performance`（per-regime 对比）
  - `skills.bootstrap_distribution`（差异显著性）
  - `ops.cumulative_returns`、`ops.drawdown_curve`
- **跨项目用例**:
  - Helios（教育用）
  - Helixa（评估策略偏离）
  - 任何含规则系统的项目
- **来源**: Vibe-Trading "Shadow Account" 启发
- **关键疑问**:
  - 规则集表达方式（DSL? 函数？）→ API 设计待 evaluation
  - 是否真通用 → **待 evaluation 验证**

---

### 组 2：Regime / 市场状态分析（Regime & Market State）—— 3 个

#### 2.1 `features.regime_replay_search` ⭐ 强候选

- **用途**：给定当前市场 panel，找历史最相似的 regime + 后续演化分布
- **核心逻辑**（对应 Helios §17）:
  - 输入：current panel signature + historical_db
  - 输出：top-K 相似时期 + 各时期后续 N 天 returns 分布 + 整体 forward distribution
- **调用 skills/ops**:
  - `skills.historical_analogy_search`（核心：DTW + Wasserstein + cosine ensemble）
  - `skills.regime_transition_analysis`（regime 切换历史）
  - `skills.bootstrap_distribution`（forward returns 分布）
  - `ops.regime_filter_data`、`ops.percentile_ci`
- **API（预判）**:
  ```python
  def regime_replay_search(
      current_panel: pd.DataFrame,
      historical_panels: list[pd.DataFrame],
      *,
      forward_days: int = 30,
      top_k: int = 10,
      methods: list = ["dtw", "wasserstein"],
  ) -> dict
  ```
- **跨项目用例**:
  - Helios §17 Regime Replay（核心模块）
  - Tide A 股版同样需求
  - Helixa（虽然主要是 trade，但策略评估也能用）
- **关键疑问**:
  - panel signature 如何表达？固定 schema 还是灵活 → **待 evaluation**

---

#### 2.2 `features.regime_change_detector` 强候选

- **用途**：实时检测 regime 切换 + 提供切换前后差异分析
- **核心逻辑**:
  - 输入：连续时序 panel
  - 检测 regime 切换点（基于已有 regime label series 或调用外部 HMM）
  - 切换前后窗口对比（distribution shift）
- **调用 skills/ops**:
  - `skills.distribution_shift_test`（切换前后分布对比）
  - `skills.regime_transition_analysis`（切换矩阵历史）
  - `ops.regime_filter_data`、`ops.regime_label_align`
- **API（预判）**:
  ```python
  def regime_change_detector(
      data: pd.DataFrame,
      regime_labels: pd.Series,
      *,
      window_before: int = 30,
      window_after: int = 30,
      metrics: list = ["sharpe", "vol", "skew"],
  ) -> dict
  ```
- **跨项目用例**:
  - Helios §17 BOCPD post-mortem
  - Helixa 策略 lifecycle（regime 切换 → 策略调整）
  - Tide / Selene
- **关键疑问**:
  - 与 oskill.regime_transition_analysis 边界？→ Layer 3 = 端到端业务功能，含报告生成

---

#### 2.3 `features.regime_conditional_dashboard_data` 中候选

- **用途**：生成 regime-conditional 的多维度对比数据（per-regime stats + 转移 + duration）
- **核心逻辑**:
  - 输入：returns + regime_labels + 多个 metric 选择
  - 输出：完整 dashboard 数据结构（regime × metric matrix + transition + duration + analogy refs）
- **调用 skills/ops**:
  - `skills.regime_aware_performance`
  - `skills.regime_transition_analysis`
  - `skills.distribution_shift_test`
- **API（预判）**:
  ```python
  def regime_conditional_dashboard_data(
      returns: pd.Series,
      regime_labels: pd.Series,
      *,
      metrics: list = ["sharpe", "drawdown", "var_95", "skew"],
      include_transitions: bool = True,
  ) -> dict
  ```
- **跨项目用例**:
  - Helios / Tide / Helixa 都有 regime dashboard 需求
- **关键疑问**:
  - 与 skills.regime_aware_performance 边界？→ Layer 3 = 完整 dashboard 数据，包含多个 skills 的组装
  - 是否过度抽象（业务模块自己组装更好）？→ **待 evaluation 验证**

---

### 组 3：策略验证与评估（Strategy Validation & Evaluation）—— 3 个

#### 3.1 `features.strategy_backtest_report` ⭐ 强候选

- **用途**：完整 backtest 报告（CPCV + WFO + per-regime + bootstrap CI + factor attribution）
- **核心逻辑**:
  - 输入：strategy returns / trades + benchmark + regime_labels（可选）+ factor data（可选）
  - 输出：完整学术级 backtest 报告（PSR/DSR + CPCV path distribution + per-regime breakdown + 因子归因）
- **调用 skills/ops**:
  - `skills.cpcv_pipeline`
  - `skills.walk_forward_optimization`
  - `skills.psr_dsr`
  - `skills.bootstrap_sharpe`
  - `skills.regime_aware_performance`（如有 regime_labels）
  - `skills.factor_attribution`（如有 factor data）
- **API（预判）**:
  ```python
  def strategy_backtest_report(
      strategy_returns: pd.Series,
      *,
      benchmark_returns: pd.Series | None = None,
      regime_labels: pd.Series | None = None,
      factor_returns: pd.DataFrame | None = None,
      cpcv_config: dict | None = None,
      wfo_config: dict | None = None,
  ) -> dict
  ```
- **跨项目用例**:
  - Helios §9 完整 backtest（核心）
  - Helixa 策略评估
  - Selene quant A/B 测试
  - Tide
- **关键疑问**:
  - 报告深度（多少 sections）→ **待 evaluation 验证**

---

#### 3.2 `features.strategy_decay_monitor` 强候选

- **用途**：策略衰退监控（rolling Sharpe + Mann-Kendall trend + alert threshold）
- **核心逻辑**:
  - 输入：strategy live returns（持续更新）
  - 输出：当前 decay state + rolling metrics + 趋势检验 + 是否触发 alert
- **调用 skills/ops**:
  - `skills.bootstrap_sharpe`（rolling）
  - `ops.mann_kendall_trend`
  - `skills.distribution_shift_test`（live vs historical baseline）
  - `ops.zscore_normalize`
- **跨项目用例**:
  - Helios §9.8 Strategy Decay
  - Helixa 策略 lifecycle 监控
  - Selene
- **关键疑问**:
  - "decay state"如何定义（state machine 4 状态？或连续度量？）→ **待 evaluation 验证**

---

#### 3.3 `features.factor_attribution_report` 中候选

- **用途**：完整因子归因报告（Fama-French 多模型对比 + bootstrap CI + 残差分析）
- **核心逻辑**:
  - 输入：asset returns + 多个 factor sets
  - 输出：每个 factor model 的 α/β + 残差检验 + 模型对比（最优 R² / 最稳健 alpha）
- **调用 skills/ops**:
  - `skills.factor_attribution`（核心，多次调用不同 factor set）
  - `skills.distribution_shift_test`（残差检验）
  - `ops.mann_kendall_trend`（rolling alpha trend）
- **跨项目用例**:
  - Helios §18.4
  - Helixa 策略风险归因
  - Tide A 股因子分析
- **关键疑问**:
  - 与 skills.factor_attribution 边界？→ Layer 3 = 多模型对比 + 残差分析报告，不只是单次回归

---

### 组 4：信号与告警（Signals & Alerts）—— 2 个

#### 4.1 `features.alert_calibration_engine` 中候选

- **用途**：alert 系统的 calibration + Bandit feedback loop
- **核心逻辑**:
  - 输入：alert predictions + 实际 outcomes
  - 输出：calibration 报告 + per-alert-type Brier score + Beta posterior 更新（Bandit）
- **调用 skills/ops**:
  - `skills.calibration_analysis`（核心）
  - `ops.bayes_beta_update`（Bandit）
  - `ops.brier_score_decomposed`
- **API（预判）**:
  ```python
  def alert_calibration_engine(
      alerts_history: pd.DataFrame,  # alert_id, ts, predicted_prob, actual_outcome
      *,
      group_by: list = ["alert_type"],
      include_bandit_state: bool = True,
  ) -> dict
  ```
- **跨项目用例**:
  - Helios §15 Alerts（核心）
  - Helixa 信号准确性评估
  - 任何含 alert 系统的项目
- **关键疑问**:
  - alert schema 通用性（不同项目 alert 字段不同）→ **待 evaluation 验证**

---

#### 4.2 `features.thesis_invalidation_monitor` 中候选

- **用途**：thesis 失效监控（基于 Brier Score + 阈值）
- **核心逻辑**:
  - 输入：thesis predictions（持续）+ 实际 outcomes
  - 输出：thesis 是否仍有效 + Brier 三分量趋势 + 失效预警
- **调用 skills/ops**:
  - `skills.calibration_analysis`
  - `ops.mann_kendall_trend`（Brier 趋势）
  - `ops.brier_score_decomposed`
- **跨项目用例**:
  - Helios §16 Watchlist thesis
  - Helixa 策略 hypothesis 监控
- **关键疑问**:
  - 与 features.alert_calibration_engine 重叠多少？→ thesis 是更长期的预测，alert 是即时的，**待 evaluation 验证**

---

### 组 5：Scenario / 风险管理（Scenario & Risk）—— 2 个

#### 5.1 `features.scenario_stress_test` 强候选

- **用途**：完整 scenario stress test（历史类比 scenario + 自定义 scenario + Monte Carlo）
- **核心逻辑**:
  - 输入：current portfolio + scenario definitions（历史日期范围 / 自定义 shock）
  - 输出：每个 scenario 下的 portfolio 表现 + VaR / ES / drawdown + scenario 之间对比
- **调用 skills/ops**:
  - `skills.historical_analogy_search`（找历史类比 scenario）
  - `skills.regime_aware_performance`（per-scenario stats）
  - `skills.bootstrap_distribution`（不确定性）
  - `ops.value_at_risk`、`ops.drawdown_curve`
- **跨项目用例**:
  - Helios §11 Scenario（核心）
  - Helixa portfolio 风险评估
  - 任何含 portfolio 的项目
- **关键疑问**:
  - portfolio schema（是否含 weights / exposures / 复杂 product）→ **待 evaluation 验证**

---

#### 5.2 `features.tail_risk_analyzer` 中候选

- **用途**：尾部风险完整分析（GARCH-EVT-VaR + tail dependency + extreme scenarios）
- **核心逻辑**:
  - 输入：returns（单资产或 portfolio）
  - 输出：parametric / historical / Cornish-Fisher / EVT 多方法 VaR/ES 对比 + tail metrics
- **调用 skills/ops**:
  - `ops.value_at_risk`（atomic VaR）
  - `ops.skew_kurt_robust`
  - `ops.kolmogorov_smirnov_test`（GPD fit goodness）
  - `skills.bootstrap_distribution`
- **跨项目用例**:
  - Helios §12 EVT（核心）
  - Helixa portfolio tail risk
  - Tide / Selene
- **关键疑问**:
  - GARCH-EVT 完整 pipeline 是否本 feature 范围？还是另起 feature？→ **待 evaluation 验证**
  - 注意：完整 GARCH-EVT 可能需要 oprim 添加 ops.garch_fit + ops.gpd_fit（Layer 1 backlog）

---

### 组 6：数据质量与诊断（Data Quality & Diagnostics）—— 2 个

#### 6.1 `features.panel_data_quality_check` 强候选

- **用途**：panel data 完整 quality check（gap + outlier + freshness + cross-source consistency）
- **核心逻辑**:
  - 输入：panel DataFrame（多字段时序）
  - 输出：每个字段的 quality 报告 + 整体 panel score + 异常列表
- **调用 skills/ops**:
  - `skills.detect_outliers_robust`
  - `skills.distribution_shift_test`（vs 历史 baseline）
  - `ops.gap_detect`
  - `ops.lag_forward_fill`
- **跨项目用例**:
  - Helios §6 panel / §14 实时数据 / §22 数据保留
  - Helixa data pipeline quality
  - Tide / Selene 数据质量
  - 任何含时序 panel 的项目
- **关键疑问**:
  - quality score 加权方式 → **待 evaluation 验证**

---

#### 6.2 `features.cross_source_consistency_check` 中候选

- **用途**：多数据源一致性检查（同一指标在不同数据源对比 + 漂移检测）
- **核心逻辑**:
  - 输入：多个数据源的同名 series（DataFrame columns 是 source 名）
  - 输出：每对 source 的一致性 score + 漂移检测 + 最可信 source 推荐
- **调用 skills/ops**:
  - `skills.distribution_shift_test`
  - `skills.detect_outliers_robust`
  - `ops.pearson_spearman_corr`
- **跨项目用例**:
  - Helios §14 multi-source feed
  - 任何含多数据源的项目
- **关键疑问**:
  - 真"≥ 2 项目用"还是 Helios-only？→ **待 evaluation 验证**

---

### 组 7：相似度与检索（Similarity & Search）—— 2 个

#### 7.1 `features.smart_peer_finder` ⭐ 强候选

- **用途**：智能同业检索（找历史/当前最相似的资产或时段）
- **核心逻辑**:
  - 输入：query asset/period + candidate pool
  - 输出：top-K 相似 + 多维相似度 breakdown + ranking 解释
- **调用 skills/ops**:
  - `skills.historical_analogy_search`
  - `ops.cosine_similarity_batch`、`ops.euclidean_distance_matrix`
  - `ops.percentile_rank`
- **跨项目用例**:
  - Helios §18.5 智能同业（核心）
  - Helixa（找相似历史时段做策略验证）
  - Tide A 股 peer 分析
- **关键疑问**:
  - asset feature schema（如何定义 panel signature）→ **待 evaluation 验证**

---

#### 7.2 `features.event_cascade_clusterer` 中候选

- **用途**：事件级联聚类（识别相关 news events 形成 cluster + 影响范围）
- **核心逻辑**:
  - 输入：events DataFrame（含 embedding / 内容 / 时间）
  - 输出：cluster 分组 + 每 cluster 描述 + 影响时间范围
- **调用 skills/ops**:
  - `ops.cosine_similarity_batch`（embedding 相似度）
  - `skills.detect_outliers_robust`（异常事件）
  - DBSCAN（sklearn 直接调，不在 oprim 内）
- **跨项目用例**:
  - Helios §19.5 Event Cascade（核心）
  - 任何含 news/event 的项目
- **关键疑问**:
  - DBSCAN 用 sklearn 直接调是否合规？→ Layer 3 允许（仅 Layer 1/2 不允许绕过）
  - 真"≥ 2 项目用"还是 Helios news NLP only？→ **待 evaluation 验证**

---

### 组 8：报告与导出（Report & Export）—— 2 个

#### 8.1 `features.standardized_performance_report` 强候选

- **用途**：标准化性能报告（HTML/JSON/Markdown 三格式）
- **核心逻辑**:
  - 输入：returns + 各种 metrics 选择
  - 输出：完整报告，含 Sharpe / drawdown / per-regime / factor attribution / calibration
- **调用 skills/ops**:
  - 大量调用 features 层（**等等，这违反"不调其它 Layer 3"原则**）
  - 修正版本：直接调 skills + ops，不调其它 features
- **关键疑问** ⚠:
  - 这是真 Layer 3 还是"业务模块层"？
  - 如果 features 之间不能互相调用，这个 feature 内部要重复一些 skills 调用——是否值得？
  - **强烈建议 evaluation 阶段重新评估**

---

#### 8.2 `features.tradingview_signal_export` 中候选

- **用途**：把 strategy signals 导出为 TradingView Pine Script 格式
- **核心逻辑**:
  - 输入：signals DataFrame（ts, signal_value, ...）
  - 输出：Pine Script v6 代码（可直接粘贴 TradingView）
- **调用 skills/ops**:
  - 几乎不调用 oprim/skills（主要是文本生成）
- **跨项目用例**:
  - Helios（用户在 TradingView 上看 Helios 信号）
  - Helixa（策略可视化）
- **来源**: Vibe-Trading "/pine" 启发
- **关键疑问** ⚠:
  - 不调用 oprim/skills，是否符合 Layer 3 定义？
  - Layer 3 不强制必须用 skills，但应该有"加值"
  - **可能是工具库 (helios-tools) 不是 omodul**——**待 evaluation 验证**

---

## 3. 候选汇总

```yaml
total_candidates: 16

distribution_by_group:
  组 1 交易行为分析:     2 (1 强 + 1 中)
  组 2 Regime / 市场状态: 3 (2 强 + 1 中)
  组 3 策略验证与评估:    3 (2 强 + 1 中)
  组 4 信号与告警:        2 (0 强 + 2 中)
  组 5 Scenario / 风险:   2 (1 强 + 1 中)
  组 6 数据质量诊断:      2 (1 强 + 1 中)
  组 7 相似度与检索:      2 (1 强 + 1 中)
  组 8 报告与导出:        2 (1 待评估 + 1 待评估)

confidence_distribution:
  强候选 (Spec 多次提及 + 跨项目明确):  9 个
  中候选 (跨项目预判, 待 evaluation 验证): 5 个
  待重新评估 (可能违反 Layer 3 定义):     2 个

key_insights:
  1. 16 个候选中只有 9 个是"强候选"
  2. 7 个需要 evaluation extraction 阶段验证
  3. 2 个候选 (8.1 / 8.2) 可能根本不是 Layer 3
  4. 实际 Layer 3 1.0 范围估计 12-15 个 (排除待重新评估的)
```

---

## 4. 与 Layer 1/2 调用关系总览

```yaml
features_to_skills_map:

  features 大量调用的 skills:
    skills.historical_analogy_search:       3 个 features (regime_replay, smart_peer, scenario_stress)
    skills.distribution_shift_test:         4 个 features (regime_change, panel_quality, thesis_invalidation, cross_source)
    skills.regime_aware_performance:        3 个 features (shadow_account, regime_change, regime_dashboard)
    skills.bootstrap_distribution:          3 个 features
    skills.calibration_analysis:            2 个 features (alert_calibration, thesis_invalidation)
    skills.cpcv_pipeline:                   1 个 (strategy_backtest_report)
    skills.factor_attribution:              2 个 features
    skills.bootstrap_sharpe:                2 个 features
    skills.psr_dsr:                         1 个 (strategy_backtest_report)
    skills.regime_transition_analysis:      2 个 features
    skills.detect_outliers_robust:          4 个 features
    skills.regime_aware_rolling:            1 个 (strategy_decay_monitor)
    skills.walk_forward_optimization:       1 个 (strategy_backtest_report)

  features 直接调用的 ops (不通过 skills):
    ops.regime_filter_data:        2 features (作为补充)
    ops.zscore_normalize:          2 features
    ops.percentile_rank:           2 features
    ops.percentile_ci:             2 features
    ops.value_at_risk:             1 (tail_risk_analyzer)
    ops.drawdown_curve:            1 (shadow_account)
    ops.cumulative_returns:        1 (shadow_account)
    ops.mann_kendall_trend:        2 features
    ops.brier_score_decomposed:    2 features (alert + thesis)
    ops.bayes_beta_update:         1 (alert_calibration)
    ops.gap_detect:                1 (panel_quality)
    ops.lag_forward_fill:          1 (panel_quality)
    ops.cosine_similarity_batch:   2 features
    ops.euclidean_distance_matrix: 1 (smart_peer)
    ops.pearson_spearman_corr:     1 (cross_source)
    ops.skew_kurt_robust:          1 (tail_risk)
    ops.kolmogorov_smirnov_test:   1 (tail_risk)
    ops.regime_label_align:        1 (regime_change)

key_observations:
  1. oskill 是 features 的主要 building block (符合方法论)
  2. 部分 features 直接调 oprim 是合理的 (skills 不一定满足所有需求)
  3. skills.distribution_shift_test 复用率最高 (4 features) → 验证是真实需求
  4. skills.cpcv_pipeline / walk_forward_optimization 仅 1 feature 用 → 但 skill 本身价值高
```

---

## 5. 跨项目复用预测

```yaml
cross_project_reuse_prediction:

  helios_22_chapters_usage:
    强候选都 ≥ 1 章使用
    示例 mapping:
      §13 持仓:         features.shadow_account_simulator (可选)
      §15 Alerts:       features.alert_calibration_engine
      §16 Watchlist:    features.thesis_invalidation_monitor
      §17 Replay:       features.regime_replay_search + regime_change_detector
      §11 Scenario:     features.scenario_stress_test + tail_risk_analyzer
      §12 EVT:          features.tail_risk_analyzer
      §18 Stock:        features.factor_attribution_report + smart_peer_finder
      §19 News:         features.event_cascade_clusterer
      §6 panel:         features.panel_data_quality_check
      §14 实时:         features.cross_source_consistency_check
      §9 Backtest:      features.strategy_backtest_report

  helixa_usage_predicted:
    几乎每个强候选都用得上
    特别核心:
      features.trade_journal_analyzer (策略 paper trading 自我诊断)
      features.strategy_decay_monitor (live 监控)
      features.strategy_backtest_report (策略验证)
      features.scenario_stress_test (portfolio 风险)
      features.alert_calibration_engine (信号准确性)

  tide_usage_predicted:
    与 Helios 高度重叠 (A 股 vs BTC 业务相似)
    几乎所有强候选都用

  selene_usage_predicted:
    重点:
      features.strategy_backtest_report (A/B 测试核心)
      features.factor_attribution_report
      features.bootstrap_distribution (统计显著性)

  reuse_rate_estimate:
    16 候选 × 平均 2.5 项目用 = 40 个 reuse 实例
    每个 feature 平均省 5-15 天工程量 (相比业务模块自己写)
    总省工程量: 200-600 天 (累计 4 项目)
    
    单 Layer 3 库 1.0 投入估计: 60-90 天 (16 features × 5-8 天)
    ROI: 200-600 / 60-90 = 3-10x
```

---

## 6. 待 Evaluation Extraction 阶段验证的关键问题

这一节是**最重要的**——清单不是终点，evaluation extraction 才能验证：

```yaml
critical_questions_for_evaluation:

  q1_layer_3_vs_business_module_boundary:
    某些候选可能根本不属于 Layer 3:
      - features.standardized_performance_report (是否绕过"不调其它 Layer 3")
      - features.tradingview_signal_export (是否归 helios-tools)
      - features.regime_conditional_dashboard_data (是否过度抽象)
    需要 evaluation 阶段重新评估

  q2_truly_cross_project_or_helios_only:
    某些候选 Spec 看起来通用, 但可能只 Helios 用:
      - features.cross_source_consistency_check
      - features.trade_journal_analyzer (Vibe-Trading 启发, 但 Helios Spec 不强)
      - features.shadow_account_simulator
    需要 evaluation 4 项目实际代码验证

  q3_api_schema_universality:
    部分 feature 的输入 schema 可能太复杂, 不真"通用":
      - features.scenario_stress_test (portfolio schema)
      - features.alert_calibration_engine (alert schema)
      - features.event_cascade_clusterer (events schema)
    需要 evaluation 阶段确定通用 schema

  q4_layer_2_completeness:
    某些 feature 实施时可能发现 oskill 缺关键 skill:
      - features.tail_risk_analyzer (可能需要新 skill: garch_evt_pipeline)
      - features.strategy_decay_monitor (可能需要新 skill: rolling_brier)
    可能触发 oskill 1.x 添加新 skill

  q5_layer_1_completeness:
    某些 feature 实施时可能发现 oprim 缺关键 op:
      - features.tail_risk_analyzer (需要 ops.garch_fit + ops.gpd_fit)
      - features.event_cascade_clusterer (需要 ops.dbscan_clustering?)
    可能触发 oprim 1.x 添加新 op
```

---

## 7. Evaluation Extraction 流程建议

```yaml
evaluation_extraction_workflow:

  phase_3_in_methodology (4-6 周):
    
    week_1_helios_btc_evaluation:
      - 评估 Helios BTC 板块代码 (§4+§5+§6 + dashboard)
      - 对照本清单, 标注:
        ✓ 真实存在 / 已实现部分
        ✗ 仅本清单预判, 实际不需要
        + 新发现 (本清单没列出的)
      - 输出: helios-btc-features-extraction.md

    week_2_3_helixa_evaluation:
      - 评估 Helixa 19 服务全量
      - 同上格式
      - 输出: helixa-features-extraction.md

    week_4_selene_evaluation:
      - 等 Selene 信息后评估
      - 输出: selene-features-extraction.md

    week_5_6_synthesis:
      - 合并 3 项目的 extraction 结果
      - 对照本清单 16 个候选
      - 决定每个候选的最终去向:
        ✅ 进入 Layer 3 1.0 (强候选 + 验证通过)
        🔄 修订 API + 进入 Layer 3 1.0
        📦 推迟到 Layer 3 1.x (不紧急 / 单项目用)
        ❌ 不入栈 (业务专用 / 抽象不到位)
        ➕ 新增 (本清单没预测到的)
      - 输出: omodul-final-list.md (类似 Oprim-Final-List.md)

  output_documents:
    - 4 个 evaluation 报告
    - 1 个最终 list
    - 触发 Layer 1/2 缺口的 backlog (可能需要 ops/skills 1.x)
```

---

## 8. 与 ADR-063 的关系

```yaml
adr_063_dependency:
  本清单 → evaluation extraction → 最终 list → 写 ADR-063 (omodul 1.0 spec)
  
  不是: 本清单 → 直接写 ADR-063
  原因: Layer 3 业务复杂度高, 必须 evaluation 验证, 不能凭空设计

adr_063_will_contain:
  与 ADR-061 / ADR-062 同样章节结构:
    1. Context (基于 evaluation 结果)
    2. Decision (12-15 个 features 最终清单)
    3. 完整 spec (每个 feature 的 API)
    4. 工程标准 (含 Layer 3 特有: 真实业务数据 dogfood)
    5. 工作量分解
    6. Cross-project usage
    7. Layer 4 业务模块前瞻
    8. Alternatives Considered
    9. Consequences
    10. Revisit Triggers
    11. References
    12. Approval

  estimated_size: 2500-3500 行 (含 12-15 features 完整 spec)
```

---

## 9. Layer 3 工程标准前瞻

这一层与 Layer 1/2 的关键差异（详细写在 ADR-063）：

```yaml
layer_3_engineering_standards_preview:

  独特要求:
    
    real_world_dogfood_mandatory:
      每个 feature 必须有 ≥ 1 个真实业务项目 dogfood 验证才能 ship 1.0
      (Layer 1/2 单元测试 + 学术对照即可, Layer 3 必须真实数据)

    api_schema_universal:
      输入用通用 schema (DataFrame / dict)
      输出用通用结果 (dict / DataFrame)
      不绑定特定项目存储 / 展示 / 业务流程

    integration_test_with_real_skills:
      不用 mock 测试 (Layer 2 用 mock)
      用真实 oskill + oprim 跑端到端

    business_complexity_documentation:
      每个 feature 必须有"业务用途"文档
      不只是技术接口, 而是"什么场景用 + 怎么用"

    versioning_with_consumer_feedback:
      Layer 3 1.0 → 1.1 修订主要来自调用方反馈
      不是凭空设计 minor

  与 Layer 1/2 一致的:
    - 测试覆盖率 ≥ 90%
    - 三大纪律 (内部不互调 / 必须用下层 / 中性设计)
    - Pydantic v2 schema
    - semantic versioning
    - 独立 standalone package
    - GitHub Packages 发布
```

---

## 10. 我对本清单的 confidence 标注

```yaml
confidence_breakdown:

  layer_3_范畴判断:    85%
    16 候选基本都符合 Layer 3 定义
    只有 features.standardized_performance_report 和 features.tradingview_signal_export 边界模糊

  跨项目复用预测:      70%
    强候选 9 个: 高 confidence
    中候选 5 个: 待 evaluation 验证
    可能 2-3 个不真"≥ 2 项目用"

  调用 ops/skills 列表: 90%
    基于已确认的 31 ops + 13 skills
    极少数 feature 可能需要新 ops/skills

  数量预测:            70%
    16 是预判, 实际 Layer 3 1.0 估计 12-15 个
    经过 evaluation 可能 +/- 3 个

  总体 confidence:     75%
    作为预判清单足够好
    但严禁直接基于此写 ADR-063
    必须经过 evaluation extraction
```

---

## 11. 一句话结论

**16 个 Layer 3 omodul 预判候选，9 个强候选 + 5 个中候选 + 2 个待重新评估，覆盖交易行为/Regime/策略验证/告警/Scenario/数据质量/相似度/报告 8 个领域。**

**本清单不是 ship-ready spec，是 evaluation extraction 阶段的对照基线。最终 Layer 3 1.0 通过 4 项目 evaluation 验证后，写入 ADR-063（估计 12-15 个 features）。**

---

## 附录：与 ADR-061/062 的对比

```yaml
three_proposals_comparison:

  helios_atomic_ops_proposal (Layer 1):
    候选数量: 31
    confidence: 95% (atomic 数学算子, 通用性强)
    需要 evaluation 验证: 否 (直接进入实施)
    ship 路径: 已写 ADR-061 + 31 prompts

  oskill_proposal (Layer 2):
    候选数量: 13
    confidence: 90% (基于 Layer 1 组合, 相对清晰)
    需要 evaluation 验证: 否 (直接进入实施)
    ship 路径: 已写 ADR-062 + 13 prompts

  omodul_proposal (Layer 3) ⭐ 本文档:
    候选数量: 16 (预判)
    confidence: 75% (业务复杂度高, 需要验证)
    需要 evaluation 验证: 是 (4 项目 extraction 阶段)
    ship 路径: 本清单 → evaluation → 最终 list → ADR-063 → 12-15 prompts

key_difference:
  Layer 1/2 已 ship-ready (ADR + prompts 都写完)
  Layer 3 必须先 evaluation extraction, 才能写 ADR-063
  
  这是因为 Layer 3 业务复杂度的客观要求, 不是文档完整度问题
```
