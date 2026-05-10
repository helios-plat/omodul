# omodul 元模块 Layer 3 — 16 个 CC 实施 Prompt

**用途**：每个元模块一个独立 CC prompt，复制粘贴给 Claude Code 即可实施。

**前置依赖**：
- oprim 1.0 已 ship（pip install oprim）
- oskill 1.0 已 ship（pip install oskill）
- 31 atomic ops + 13 meta-skills 全部可调用

**版本说明**：
- 这是 omodul **0.1 候选实施版**，不是 1.0 正式版
- 1.0 正式版需要至少 2 个调用方 dogfood 验证后才发布
- 实施时遇到设计不便要立即反馈，触发 minor version 修订

**使用方式**：
- 多 CC instance 并行：每个 instance 跑一个元模块
- 单 CC instance 串行：按组依次跑
- Wiki review：每个元模块一个独立 PR

**全局规则**（每个 prompt 都隐含）：
1. FULL AUTO MODE - 不中途问问题
2. 单元模块单 PR - 不混合多个
3. 必须含：实现 + 测试 + 文档
4. **不允许 import 任何 omodul.\***（Layer 3 纪律：内部互不调用）
5. **必须 import oskill.\* 和/或 oprim.\* 实现核心逻辑**
6. 测试覆盖率 ≥ 90%
7. **真实业务数据 dogfood 验证**（Layer 3 特有要求）
8. Pydantic v2 输入输出

---

## 共用 Prompt Header（每个 prompt 复制时都加上这段）

```
======================================================================
FULL AUTO MODE
- 不要中途问问题
- 自行决策推进
- 失败才停下来报告
- 任务全部完成后只汇报一次
======================================================================

# omodul 元模块 Layer 3 实施任务

## 项目位置
~/projects/omodul/  (独立仓库)

## 必须先读的参考
- ~/projects/omodul/docs/DESIGN.md (如已存在)
- ~/projects/omodul/ADR-063.md (如已存在, 否则参考本 prompt)
- ~/projects/oprim/docs/INDEX.md (Layer 1 已实现 ops 索引)
- ~/projects/oskill/docs/INDEX.md (Layer 2 已实现 skills 索引)
- 本任务的 Spec (在下面 "任务" 节)

## 全局红线 (must not violate)
1. **禁止 import omodul.*** (Layer 3 纪律: 内部互不依赖)
2. **必须 import oskill.* 和/或 oprim.*** 实现核心逻辑
3. 仅允许直接 import: oprim, oskill, numpy, scipy, pandas, sklearn, statsmodels, pydantic
4. 禁止读环境变量 / 文件 / 网络
5. 禁止修改输入数据 (pure function)
6. 禁止 silent fail (异常输入必须 raise ValueError + 明确消息)
7. 输入用通用 schema (DataFrame / dict, 不绑特定项目存储)
8. 输出用通用结果 (dict / DataFrame, 不绑特定项目展示)

## 输出标准
1. 实现代码: omodul/<group>.py 中追加该元模块
2. 测试: tests/test_<group>.py 中追加该元模块测试
3. 文档: docs/<group>.md 中追加该元模块文档
4. 更新 omodul/__init__.py 显式 export

## 测试要求 (Layer 3 特有: 比 Layer 1/2 严格)
- 单元测试覆盖率 ≥ 90%
- happy path ≥ 3 用例
- edge cases ≥ 3 用例
- exception cases ≥ 3 用例
- **集成测试**: 不用 mock, 用真实 oskill + oprim 跑端到端
- **真实业务数据测试**: 至少 1 个真实金融数据样本 (e.g. SPY 252 日 returns + dummy regime labels)
- 性能基准 (如适用)

## PR 提交格式
git checkout -b feat/features-<feature_name>
git add omodul/<group>.py tests/test_<group>.py docs/<group>.md omodul/__init__.py
git commit -m "feat(features): add features.<feature_name>"
git push -u origin feat/features-<feature_name>

## 完成报告
报告以下内容到 stdout:
- 元模块名称
- 调用的 oskill + oprim 列表
- 文件路径 (实现 / 测试 / 文档)
- 测试覆盖率 (%)
- 真实数据测试结果
- 性能 benchmark (如适用)
- 已知限制
- API 设计反馈 (如发现 oskill/ops 不便)
- PR URL (如已 push)
======================================================================
```

---

# 组 1：交易行为分析（Trade Behavior Analysis）—— 2 个

---

## Prompt 1.1: `features.trade_journal_analyzer`

```
[复用全局 Header]

任务: 实现 omodul.trade_journal_analyzer

业务用途: 输入交易记录, 诊断 4 类常见行为偏差 + 行为模式分析

调用的 oskill + ops (必须用):
  - oskill.detect_outliers_robust  (异常交易检测)
  - oskill.distribution_shift_test (行为模式漂移)
  - oskill.bootstrap_distribution  (统计量 CI)
  - oprim.percentile_rank
  - oprim.zscore_normalize

数学逻辑:

  4 类 bias 定义:

  1. Disposition Effect (处置效应):
     - PGR (Proportion of Gains Realized) = 已实现盈利交易数 / (已实现盈利 + 未实现盈利)
     - PLR (Proportion of Losses Realized) = 已实现亏损交易数 / (已实现亏损 + 未实现亏损)
     - DE_score = PGR - PLR (>0 = 处置效应强, <0 = 持仓亏损长)
     - 学术参考: Odean 1998《Are Investors Reluctant to Realize Their Losses?》

  2. Overtrading (过度交易):
     - turnover_ratio = total_trade_volume / avg_portfolio_value
     - 与基准 (e.g. monthly turnover < 30%) 对比
     - 用 zscore_normalize 标准化历史 turnover 分布
     - 高 z-score = overtrading

  3. Chasing Momentum (追涨杀跌):
     - 计算每笔买入前 N 日 price momentum
     - 高正动量 + 买入 = chasing up
     - 高负动量 + 卖出 = chasing down (panic sell)
     - score = correlation(prior_momentum, trade_direction)

  4. Anchoring (锚定效应):
     - 计算每笔卖出价格 vs entry price 的距离分布
     - 平仓价集中在 entry price ± 5% = anchoring 强
     - 用 distribution_summary 描述

API 签名:
  def trade_journal_analyzer(
      trades: pd.DataFrame,
      *,
      diagnostics: list = ["disposition", "overtrading", "chasing", "anchoring"],
      benchmark_returns: pd.Series | None = None,
      lookback_momentum: int = 5,
      bootstrap_ci: bool = True,
      n_bootstrap: int = 1000,
      random_state: int | None = None,
  ) -> dict

通用 trades schema (输入):
  必需 columns:
    - timestamp (pd.Timestamp)
    - symbol (str)
    - side (Literal["buy", "sell"])
    - quantity (float, positive)
    - price (float)
  可选 columns:
    - entry_price (float, for sells)
    - holding_days (int)
    - pnl (float, realized for sells)

实现要求:
1. trades schema 验证 (必需 columns 存在)
2. timestamp 未排序 → 内部排序
3. 每个 diagnostic 独立计算, 不依赖其它
4. bootstrap_ci=True 时, 用 oskill.bootstrap_distribution 给关键指标 CI
5. 异常交易用 oskill.detect_outliers_robust 检测 (e.g. 极端 size / 极端 holding period)
6. 行为漂移用 oskill.distribution_shift_test 对比前后两段时期

返回 schema:
  {
    "diagnostics": {
      "disposition": {
        "pgr": float, "plr": float, "de_score": float,
        "ci_low": float, "ci_high": float,
        "interpretation": str,  # "strong_disposition" / "neutral" / "loss_aversion_low"
        "n_trades_used": int,
      },
      "overtrading": {
        "turnover_ratio": float,
        "zscore_vs_history": float,
        "interpretation": str,
        "n_periods": int,
      },
      "chasing": {
        "momentum_correlation": float,
        "ci_low": float, "ci_high": float,
        "interpretation": str,  # "chasing_up" / "contrarian" / "neutral"
      },
      "anchoring": {
        "exit_price_concentration": float,  # within ±5% of entry
        "anchor_zones_identified": int,
        "interpretation": str,
      },
    },
    "behavior_metrics": {
      "n_trades_total": int,
      "avg_holding_days": float,
      "win_rate": float,
      "profit_factor": float,
      "outlier_trades_detected": int,
      "behavior_drift_detected": bool,  # if 比较前后两期
    },
    "summary_report": {
      "primary_biases": list[str],  # 最显著的 1-2 个 bias
      "n_trades_analyzed": int,
      "analysis_period": (pd.Timestamp, pd.Timestamp),
      "warnings": list[str],
    },
  }

测试要求 (≥ 14 用例):
1. 简单 trades (10 笔买卖, 无 bias) → 各 diagnostic neutral
2. 强 disposition trades (大量提前平盈利, 持续亏损) → DE_score > 0.2
3. overtrading trades (高 turnover) → zscore > 2
4. chasing trades (买入前 5 日均 +5%) → momentum_correlation > 0.5
5. anchoring trades (卖出集中在 entry ±5%) → concentration > 0.6
6. n_trades < 30 raise warning (统计不稳)
7. trades 缺必需 column raise
8. timestamp 非排序 → 自动排序
9. diagnostics=["disposition"] 仅一个
10. bootstrap_ci=True 测试 (CI 字段存在)
11. bootstrap_ci=False 测试
12. **集成测试**: 不 mock, 用真实 oskill 跑端到端
13. **真实数据测试**: 模拟 SPY 上 6 个月交易, 验证输出合理
14. **学术对照**: Odean 1998 disposition effect 定义验证
15. 输出 schema 完整

性能要求:
- 1000 trades, 全部 diagnostics + bootstrap: < 2s

文件位置:
- 实现: omodul/behavior.py
- 测试: tests/test_behavior.py
- 文档: docs/behavior.md
```

---

## Prompt 1.2: `features.shadow_account_simulator`

```
[复用全局 Header]

任务: 实现 omodul.shadow_account_simulator

业务用途: 基于规则的"影子账户"模拟 ("如果你严格按规则交易会怎样")

调用的 oskill + ops (必须用):
  - oskill.regime_aware_performance  (per-regime 对比)
  - oskill.bootstrap_distribution    (差异显著性)
  - oprim.cumulative_returns
  - oprim.drawdown_curve

数学逻辑:

  Shadow Account 核心:
    1. 输入: 实际交易历史 + rule_fn (规则函数)
    2. 对每个时点, rule_fn 给出"应该的"交易决策
    3. 模拟 shadow portfolio (按规则交易)
    4. 比较 actual vs shadow:
       - 总 P&L 差异
       - 规则违反次数 (actual ≠ shadow)
       - 各 regime 下的差异

API 签名:
  def shadow_account_simulator(
      actual_trades: pd.DataFrame,
      market_data: pd.DataFrame,  # ts × symbol → price
      rule_fn: Callable[[pd.Timestamp, dict], dict],
      *,
      initial_capital: float = 100000.0,
      regime_labels: pd.Series | None = None,
      bootstrap_significance: bool = True,
      n_bootstrap: int = 1000,
  ) -> dict

rule_fn 接口:
  def rule_fn(
      timestamp: pd.Timestamp,
      context: dict,  # {portfolio_state, market_data_so_far, ...}
  ) -> dict | None:  # {symbol, side, quantity} or None
  # rule_fn 是 caller 自定义的规则
  # 返回 None 表示该时点不交易

实现要求:
1. 模拟 shadow portfolio:
   - 初始 capital = initial_capital
   - 按 actual_trades 时间顺序遍历
   - 每个 timestamp 调 rule_fn(ts, context) 获取规则决策
   - 同时记录 actual_trades 的实际决策
   - 比较两者
2. 累计两条 equity curve:
   - actual_equity (按实际交易)
   - shadow_equity (按规则交易)
3. 用 oprim.cumulative_returns + drawdown_curve 计算两条 curve 的指标
4. regime_labels 给定时, 用 oskill.regime_aware_performance 拆解 per-regime 对比
5. bootstrap_significance=True 时, 用 oskill.bootstrap_distribution 给 P&L 差异 CI
6. rule violation count: actual ≠ shadow 的次数

返回 schema:
  {
    "actual_performance": {
      "total_return": float, "sharpe": float,
      "max_drawdown": float, "n_trades": int,
    },
    "shadow_performance": {
      "total_return": float, "sharpe": float,
      "max_drawdown": float, "n_trades": int,
    },
    "comparison": {
      "pnl_difference": float,             # shadow - actual
      "pnl_difference_ci": (float, float) | None,
      "rule_violations": int,
      "rule_violation_rate": float,
      "deviation_significant": bool | None, # if bootstrap_significance
    },
    "regime_breakdown": pd.DataFrame | None,  # if regime_labels given
    "actual_equity_curve": pd.Series,
    "shadow_equity_curve": pd.Series,
  }

测试要求 (≥ 12 用例):
1. rule_fn 与 actual 完全一致: rule_violations=0, pnl_difference=0
2. rule_fn 完全不交易 (始终 None): shadow_equity = initial_capital
3. rule_fn 逆向交易: shadow vs actual 差异显著
4. regime_labels 给定: regime_breakdown 不为 None
5. regime_labels=None: regime_breakdown=None
6. bootstrap_significance=True 测试 (CI 存在)
7. actual_trades 为空 raise warning
8. market_data 缺失某 symbol raise
9. rule_fn 返回非法格式 raise
10. **集成测试**: 真实 SPY 数据 + 简单规则 (e.g. "SPY drawdown > 5% 时减仓 50%")
11. **集成测试**: 不 mock, 验证 regime_aware_performance 真实调用
12. 性能: 1000 timestamps, 简单 rule_fn: < 5s
13. 验证 actual_equity_curve 与 actual_trades 实际 P&L 一致
14. 验证 shadow_equity_curve 单调对应 rule 决策

性能要求:
- 1000 timestamps, simple rule_fn: < 5s

文件位置: 同上 (behavior.py)
```

---

# 组 2：Regime / 市场状态分析（Regime & Market State）—— 3 个

---

## Prompt 2.1: `features.regime_replay_search`

```
[复用全局 Header]

任务: 实现 omodul.regime_replay_search

业务用途: 给定当前市场 panel, 找历史最相似的时期 + 后续演化分布

调用的 oskill + ops (必须用):
  - oskill.historical_analogy_search   (核心: DTW + Wasserstein ensemble)
  - oskill.regime_transition_analysis  (regime 切换历史)
  - oskill.bootstrap_distribution      (forward returns 分布)
  - oprim.regime_filter_data
  - oprim.percentile_ci

数学逻辑:

  Regime Replay 核心:
    1. 输入: current panel + historical_panels (历史多个时期的 panel)
    2. 用 historical_analogy_search 找 top-K 相似历史 panel
    3. 对每个相似 panel, 提取其后续 N 天 returns
    4. 聚合 top-K 后续 returns 分布 → forward distribution
    5. (可选) 用 regime_transition_analysis 提取 regime 切换历史

API 签名:
  def regime_replay_search(
      current_panel: pd.DataFrame,
      historical_panels: list[dict],
      # 每个 dict: {"panel": pd.DataFrame, "forward_returns": pd.Series, "regime_labels": pd.Series | None}
      *,
      forward_days: int = 30,
      top_k: int = 10,
      methods: list = ["dtw", "wasserstein"],
      ensemble: Literal["mean_rank", "borda", "weighted"] = "mean_rank",
      bootstrap_forward_ci: bool = True,
  ) -> dict

实现要求:
1. 输入验证:
   - current_panel 是 DataFrame
   - historical_panels 是 list, 每个元素是 dict 含 "panel" 和 "forward_returns"
   - forward_returns 长度 ≥ forward_days
2. 调用 oskill.historical_analogy_search:
   - query = current_panel
   - historical_db = [hp["panel"] for hp in historical_panels]
   - 拿到 top_k 结果
3. 对每个 top-K, 提取对应 forward_returns[:forward_days]
4. 聚合 top-K forward_returns:
   - 每天 (day_t) 的 returns 分布: top_k 个值
   - 用 oprim.percentile_ci 给每天分布的 5/50/95 quantile
5. bootstrap_forward_ci=True 时:
   - 对累计 forward returns 用 oskill.bootstrap_distribution
6. (可选) regime_labels 给定时, 用 oskill.regime_transition_analysis

返回 schema:
  {
    "top_k_matches": list[dict],  # 来自 historical_analogy_search
    "forward_distribution": pd.DataFrame,
    # columns: day, q_05, q_25, q_50, q_75, q_95, mean, std
    "cumulative_forward": {
      "expected_return_at_horizon": float,
      "ci_low": float, "ci_high": float,
      "probability_positive": float,
    },
    "regime_transition_summary": dict | None,
    "n_matches_used": int,
    "search_methods_used": list[str],
  }

测试要求 (≥ 12 用例):
1. 简单测试: 5 个 historical_panels, current 与 panel[0] 相同 → panel[0] 是 top 1
2. forward_distribution shape: (forward_days, 7) [day + 5 quantiles + mean + std]
3. 验证 q_05 < q_50 < q_95 (单调)
4. top_k=3 vs top_k=10 forward_distribution 差异
5. methods=["dtw"] vs ["wasserstein"] 数值差异
6. bootstrap_forward_ci=True 测试 (cumulative_forward 含 ci)
7. forward_returns 长度不足 raise
8. historical_panels 为空 raise
9. **集成测试**: 真实数据 (BTC 历史 panel + forward returns), 端到端跑通
10. **集成测试**: 不 mock, 验证 historical_analogy_search 真实调用
11. **集成测试**: 不 mock, 验证 bootstrap_distribution 真实调用
12. 性能: 100 historical_panels (panel 长度=60), forward_days=30, top_k=10: < 30s
13. probability_positive ∈ [0, 1]
14. 验证 cumulative_forward.expected_return ≈ mean(top_k forward_returns 累计)

性能要求:
- 100 historical, panel=60d, forward=30, top_k=10: < 30s

文件位置:
- 实现: omodul/regime.py
- 测试: tests/test_regime.py
- 文档: docs/regime.md
```

---

## Prompt 2.2: `features.regime_change_detector`

```
[复用全局 Header]

任务: 实现 omodul.regime_change_detector

业务用途: 实时检测 regime 切换 + 提供切换前后差异分析

调用的 oskill + ops (必须用):
  - oskill.distribution_shift_test     (切换前后分布对比)
  - oskill.regime_transition_analysis  (切换矩阵历史)
  - oprim.regime_filter_data
  - oprim.regime_label_align

数学逻辑:

  Regime Change Detection 核心:
    1. 输入: 连续时序 data + regime_labels
    2. 检测 regime 切换点 (label[t] != label[t-1])
    3. 对每个切换点:
       - 提取前 window_before + 后 window_after 的 data
       - 用 distribution_shift_test 量化差异
       - 计算切换前后多个 metrics (Sharpe / vol / skew)
    4. 输出每次切换的完整分析

API 签名:
  def regime_change_detector(
      data: pd.DataFrame,
      regime_labels: pd.Series,
      *,
      window_before: int = 30,
      window_after: int = 30,
      metrics: list = ["sharpe", "vol", "skew"],
      shift_test_methods: list = ["ks", "wasserstein"],
      include_transition_history: bool = True,
  ) -> dict

实现要求:
1. data 与 regime_labels index 对齐验证
2. 检测切换点: regime_labels.shift(1) != regime_labels (排除首行)
3. 对每个切换点 (idx_t):
   - before_window = data[idx_t - window_before : idx_t]
   - after_window = data[idx_t : idx_t + window_after]
   - 用 oskill.distribution_shift_test 量化差异
   - 计算每个 metric 的 before vs after 值
4. 切换历史: 用 oskill.regime_transition_analysis
5. 边界处理: 切换点离两端 < window 时跳过

返回 schema:
  {
    "transitions": list[dict],
    # 每个 transition:
    # {
    #   "timestamp": pd.Timestamp,
    #   "from_regime": str, "to_regime": str,
    #   "shift_detected": bool,
    #   "shift_test_results": dict,
    #   "metrics_before": dict, "metrics_after": dict,
    #   "metric_changes": dict,  # after - before
    # }
    "n_transitions": int,
    "transition_history_summary": dict | None,
  }

测试要求 (≥ 12 用例):
1. 单 regime 数据 (无切换): n_transitions=0
2. 简单 2 regime 切换: 1 个 transition 输出
3. 多次切换: n 个 transitions
4. window_before/after 测试 (不同 size)
5. metrics 选择测试
6. shift_test_methods 选择测试
7. 边界切换 (前 5 行就切换): 跳过, 不 raise
8. data 与 regime_labels 不对齐 raise
9. 全 NaN regime raise
10. **集成测试**: 真实 BTC 数据 + 模拟 regime labels, 验证切换检测合理
11. **集成测试**: 不 mock, 验证 distribution_shift_test 调用
12. 验证 transitions 时间顺序
13. 验证 metric_changes = metrics_after - metrics_before
14. 性能: 10000 obs, 5 regime: < 2s

性能要求:
- 10000 obs, 5 regime: < 2s

文件位置: 同上 (regime.py)
```

---

## Prompt 2.3: `features.regime_conditional_dashboard_data`

```
[复用全局 Header]

任务: 实现 omodul.regime_conditional_dashboard_data

业务用途: 生成 regime-conditional 的 dashboard 数据 (per-regime stats + 转移 + duration)

调用的 oskill + ops (必须用):
  - oskill.regime_aware_performance
  - oskill.regime_transition_analysis
  - oskill.distribution_shift_test  (regime 之间 vs 整体)

数学逻辑:

  Regime Dashboard Data 核心:
    1. 输入: returns + regime_labels + 多个 metrics
    2. 调用 regime_aware_performance 拿到 per-regime metrics
    3. 调用 regime_transition_analysis 拿到 transition + duration
    4. 对每对 (regime_i, regime_j) 用 distribution_shift_test 验证差异显著性
    5. 整合成完整 dashboard 数据结构

API 签名:
  def regime_conditional_dashboard_data(
      returns: pd.Series,
      regime_labels: pd.Series,
      *,
      metrics: list = ["sharpe", "max_drawdown", "var_95", "skew"],
      include_transitions: bool = True,
      include_pairwise_shift: bool = True,
      annualization_factor: float = 252.0,
  ) -> dict

实现要求:
1. 调用 oskill.regime_aware_performance 拿到 per-regime DataFrame
2. include_transitions=True 时调用 oskill.regime_transition_analysis
3. include_pairwise_shift=True 时:
   - 对每对 regime, 提取 returns 子集
   - 用 oskill.distribution_shift_test 验证差异
   - 输出 pairwise matrix
4. data_per_regime 也传给 regime_transition_analysis

返回 schema:
  {
    "per_regime_metrics": pd.DataFrame,  # from regime_aware_performance
    "transition_analysis": dict | None,   # from regime_transition_analysis
    "pairwise_shift_matrix": pd.DataFrame | None,
    # rows/cols = regime, values = shift_detected (bool) or pvalue
    "summary": {
      "n_regimes": int,
      "regime_labels": list[str],
      "n_obs_per_regime": dict[str, int],
      "metrics_computed": list[str],
    },
  }

测试要求 (≥ 11 用例):
1. 简单 2 regime 测试
2. include_transitions=True/False
3. include_pairwise_shift=True/False
4. metrics 选择测试
5. 单 regime data: pairwise_shift_matrix shape (1,1)
6. data 与 labels 不对齐 raise
7. 全 NaN raise
8. **集成测试**: 不 mock, 验证三个 skill 真实调用
9. **集成测试**: 真实 SPY returns + 模拟 regime labels
10. summary schema 完整
11. n_obs_per_regime 加起来 = total n
12. pairwise_shift_matrix 对称性

性能要求:
- 5 regime, 10000 obs, 全部 metrics: < 3s

文件位置: 同上 (regime.py)
```

---

# 组 3：策略验证与评估（Strategy Validation & Evaluation）—— 3 个

---

## Prompt 3.1: `features.strategy_backtest_report`

```
[复用全局 Header]

任务: 实现 omodul.strategy_backtest_report

业务用途: 完整 backtest 报告 (CPCV + WFO + per-regime + bootstrap CI + factor attribution)

调用的 oskill + ops (必须用):
  - oskill.cpcv_pipeline
  - oskill.walk_forward_optimization
  - oskill.psr_dsr
  - oskill.bootstrap_sharpe
  - oskill.regime_aware_performance     (如有 regime_labels)
  - oskill.factor_attribution           (如有 factor data)
  - oskill.bootstrap_distribution

数学逻辑:

  完整 Backtest Report 核心:
    Required (每次都跑):
      - PSR + DSR (psr_dsr)
      - Bootstrap Sharpe distribution (bootstrap_sharpe)
      - Drawdown / cumulative returns (oprim)
    Optional (按输入决定):
      - CPCV pipeline (if cpcv_config 给定)
      - WFO (if wfo_config 给定)
      - Per-regime breakdown (if regime_labels 给定)
      - Factor attribution (if factor_returns 给定)

API 签名:
  def strategy_backtest_report(
      strategy_returns: pd.Series,
      *,
      benchmark_returns: pd.Series | None = None,
      regime_labels: pd.Series | None = None,
      factor_returns: pd.DataFrame | None = None,
      cpcv_config: dict | None = None,
      # {n_folds: 6, n_test_groups: 2, label_horizon: 0, embargo_pct: 0.01,
      #  backtest_fn: Callable | None}
      wfo_config: dict | None = None,
      # {is_window: 252, oos_window: 63, step: 63, label_horizon: 0, embargo_pct: 0.01}
      n_bootstrap: int = 1000,
      annualization_factor: float = 252.0,
      report_format: Literal["dict", "markdown"] = "dict",
  ) -> dict | str

实现要求:
1. 总是计算 (Required block):
   - psr_dsr
   - bootstrap_sharpe
   - 基础统计 (cumulative_returns, drawdown_curve, value_at_risk via ops)
2. 可选 blocks (按 config 决定):
   - cpcv_config 给定 → cpcv_pipeline
   - wfo_config 给定 → walk_forward_optimization
   - regime_labels 给定 → regime_aware_performance
   - factor_returns 给定 → factor_attribution
3. report_format='markdown' 时, 把 dict 渲染成 markdown 字符串
4. n_bootstrap 透传给 bootstrap_sharpe + psr_dsr

返回 schema (dict format):
  {
    "summary": {
      "total_periods": int,
      "annualized_return": float,
      "annualized_volatility": float,
      "annualized_sharpe": float,
      "max_drawdown": float,
      "var_95": float,
      "best_day": float, "worst_day": float,
    },
    "robust_sharpe": dict,  # bootstrap_sharpe output
    "psr_dsr": dict,         # psr_dsr output
    "cpcv": dict | None,
    "wfo": dict | None,
    "regime_breakdown": pd.DataFrame | None,
    "factor_attribution": dict | None,
    "warnings": list[str],
  }

测试要求 (≥ 14 用例):
1. 仅 strategy_returns: required block 输出
2. 加 regime_labels: regime_breakdown 不为 None
3. 加 factor_returns: factor_attribution 不为 None
4. 加 cpcv_config (含 backtest_fn): cpcv 完整 pipeline
5. 加 wfo_config: wfo 输出
6. 全部参数: 报告所有 sections
7. report_format='markdown': 返回 str
8. report_format='dict': 返回 dict
9. n=10 raise warning (太少样本)
10. 全 NaN raise
11. **集成测试**: 真实 SPY 252 日 returns, 全部参数, 端到端跑通
12. **集成测试**: 不 mock 任何 skill, 验证调用链
13. **学术对照**: 与 pyfolio.create_full_tear_sheet 输出对比 (如装)
14. 验证 summary.annualized_sharpe 与 robust_sharpe.sharpe 一致
15. warnings 字段填合理 (e.g. n<252 时给警告)

性能要求:
- n=252, 全部参数, n_bootstrap=1000: < 30s

文件位置:
- 实现: omodul/strategy.py
- 测试: tests/test_strategy.py
- 文档: docs/strategy.md
```

---

## Prompt 3.2: `features.strategy_decay_monitor`

```
[复用全局 Header]

任务: 实现 omodul.strategy_decay_monitor

业务用途: 策略衰退监控 (rolling Sharpe + Mann-Kendall trend + alert threshold)

调用的 oskill + ops (必须用):
  - oskill.bootstrap_sharpe          (rolling)
  - oskill.distribution_shift_test   (live vs baseline)
  - oprim.mann_kendall_trend
  - oprim.zscore_normalize

数学逻辑:

  Strategy Decay 核心:
    1. 输入: live returns + baseline returns (历史 IS period)
    2. Rolling Sharpe with bootstrap CI (rolling window)
    3. Mann-Kendall trend test on rolling Sharpe
    4. Distribution shift test: live vs baseline
    5. Decay state 4 状态机:
       - HEALTHY: trend stable, distribution match, Sharpe stable
       - DEGRADING: trend decreasing OR distribution shift detected
       - CRITICAL: trend strongly decreasing AND distribution shift
       - DEAD: rolling Sharpe < threshold for N consecutive periods

API 签名:
  def strategy_decay_monitor(
      live_returns: pd.Series,
      baseline_returns: pd.Series,
      *,
      rolling_window: int = 60,
      sharpe_threshold_dead: float = 0.0,
      consecutive_periods_dead: int = 30,
      annualization_factor: float = 252.0,
      mk_alpha: float = 0.05,
      shift_alpha: float = 0.05,
  ) -> dict

实现要求:
1. live_returns 长度 < rolling_window raise
2. 计算 rolling Sharpe (用 oskill.bootstrap_sharpe rolling 应用):
   - 注意: bootstrap_sharpe 是单次, 这里要 rolling apply
   - 实现: 对每个滚动窗口调用 bootstrap_sharpe (n_bootstrap 较小 e.g. 200 加速)
3. 调用 oprim.mann_kendall_trend 检测 rolling_sharpe 趋势
4. 调用 oskill.distribution_shift_test (live last N vs baseline)
5. 决定 decay_state (4 状态机)
6. 输出 decay_score (0-1, 0=healthy, 1=dead)

返回 schema:
  {
    "decay_state": Literal["HEALTHY", "DEGRADING", "CRITICAL", "DEAD"],
    "decay_score": float,
    "rolling_sharpe": pd.Series,
    "rolling_sharpe_ci_low": pd.Series,
    "rolling_sharpe_ci_high": pd.Series,
    "trend_test": dict,                       # mann_kendall output
    "distribution_shift_test": dict,           # distribution_shift_test output
    "consecutive_below_threshold": int,
    "diagnostics": {
      "trend_significant": bool,
      "trend_direction": str,
      "shift_detected": bool,
      "below_threshold_now": bool,
    },
    "alert_message": str,
  }

测试要求 (≥ 12 用例):
1. 健康策略 (live 与 baseline 一致): state=HEALTHY
2. 衰退策略 (live Sharpe 持续下降): state=DEGRADING 或 CRITICAL
3. 死策略 (live Sharpe 持续 < threshold): state=DEAD
4. distribution shift 但 trend 平: state=DEGRADING
5. trend 下降但 distribution 一致: state=DEGRADING
6. live 长度 < rolling_window raise
7. baseline 太短 raise warning
8. **集成测试**: 真实 SPY returns 模拟衰退, 端到端跑通
9. **集成测试**: 不 mock
10. decay_score ∈ [0, 1]
11. 状态转换合理 (HEALTHY → DEGRADING → CRITICAL → DEAD)
12. alert_message 非空
13. consecutive_below_threshold 计算正确
14. 性能: 1000 obs, rolling=60: < 10s

性能要求:
- 1000 obs, rolling=60, n_bootstrap=200: < 10s

文件位置: 同上 (strategy.py)
```

---

## Prompt 3.3: `features.factor_attribution_report`

```
[复用全局 Header]

任务: 实现 omodul.factor_attribution_report

业务用途: 完整因子归因报告 (Fama-French 多模型对比 + bootstrap CI + 残差分析)

调用的 oskill + ops (必须用):
  - oskill.factor_attribution        (核心, 多次调用)
  - oskill.distribution_shift_test   (残差检验)
  - oprim.mann_kendall_trend           (rolling alpha trend)
  - oprim.kolmogorov_smirnov_test      (残差正态性)

数学逻辑:

  Factor Attribution Report 核心:
    1. 输入: asset returns + 多个 factor sets
    2. 对每个 factor set 调 factor_attribution
    3. 残差分析:
       - 残差正态性 (KS test vs normal)
       - 残差自相关 (Mann-Kendall)
       - 残差分布漂移 (前后两期比较)
    4. 模型对比:
       - 最优 R²
       - 最稳健 alpha (T-stat 最高 + bootstrap CI 不跨 0)
       - 最简洁 (factors 最少且 R² 接近最大)

API 签名:
  def factor_attribution_report(
      asset_returns: pd.Series,
      factor_sets: dict[str, pd.DataFrame],
      # e.g. {"FF3": ff3_df, "FF5": ff5_df, "Carhart4": c4_df}
      *,
      bootstrap_ci_enabled: bool = True,
      n_bootstrap: int = 1000,
      include_residual_analysis: bool = True,
      include_rolling_alpha: bool = True,
      rolling_window: int = 60,
      standard_errors: Literal["ols", "white", "newey_west"] = "newey_west",
  ) -> dict

实现要求:
1. 对每个 factor_set 调用 oskill.factor_attribution
2. 残差分析 (include_residual_analysis=True):
   - 对每个模型残差用 oprim.kolmogorov_smirnov_test (vs normal)
   - 对残差用 oprim.mann_kendall_trend (检测自相关趋势)
   - 把 asset_returns 分前后两半, 用 distribution_shift_test 检测因子稳定性
3. Rolling alpha (include_rolling_alpha=True):
   - 对每个模型, 滚动窗口 OLS, 计算 rolling alpha
4. 模型对比:
   - 最优 R²: max(adj_r_squared)
   - 最稳健: alpha CI 不含 0 + tstat 最高
   - 最简洁: factors 数量 vs R² 帕累托最优

返回 schema:
  {
    "models": dict[str, dict],
    # 每个 key 是 factor_set 名:
    # {factor_attribution_output, residual_analysis: dict, rolling_alpha: pd.Series | None}
    "model_comparison": {
      "best_r_squared": str,         # factor_set 名
      "most_robust_alpha": str,
      "most_parsimonious": str,
    },
    "summary": dict,
    "warnings": list[str],
  }

测试要求 (≥ 12 用例):
1. 单 factor_set: 退化为 single attribution + 残差分析
2. 多 factor_sets (FF3 + FF5): model_comparison 选出最佳
3. include_residual_analysis=False: 残差 sections 缺失
4. include_rolling_alpha=False: rolling_alpha=None
5. asset_returns 与 factor_returns 长度不一致 raise
6. n=30 raise warning (太少)
7. **集成测试**: 真实 SPY + Fama-French 因子数据 (从 K. French 网站可下), 端到端
8. **集成测试**: 不 mock
9. **学术对照**: 已知 SPY 与 MKT 完全相关 → MKT beta ≈ 1
10. 模型对比逻辑验证: best_r_squared 真是最大
11. 残差正态性 p-value ∈ [0, 1]
12. rolling_alpha 长度 = len(returns) - rolling_window + 1
13. 性能: 1000 obs, 3 factor_sets (each 5 factors): < 10s

性能要求:
- 1000 obs, 3 factor_sets, full analysis: < 10s

文件位置: 同上 (strategy.py)
```

---

# 组 4：信号与告警（Signals & Alerts）—— 2 个

---

## Prompt 4.1: `features.alert_calibration_engine`

```
[复用全局 Header]

任务: 实现 omodul.alert_calibration_engine

业务用途: alert 系统的 calibration + Bandit feedback loop

调用的 oskill + ops (必须用):
  - oskill.calibration_analysis  (核心)
  - oprim.bayes_beta_update        (Bandit posterior)
  - oprim.brier_score_decomposed

数学逻辑:

  Alert Calibration 核心:
    1. 输入: alert history (predicted_prob + actual_outcome)
    2. 对每个 alert_type 单独跑 calibration_analysis
    3. Bandit state per alert_type:
       - 维护 Beta(α, β) posterior
       - 用 bayes_beta_update 累计更新
       - 输出 expected success rate + CI
    4. 总体 vs per-type 对比

API 签名:
  def alert_calibration_engine(
      alerts_history: pd.DataFrame,
      # required cols: alert_id, ts, alert_type, predicted_prob, actual_outcome
      *,
      group_by: list = ["alert_type"],
      n_bins: int = 10,
      include_bandit_state: bool = True,
      bandit_prior_alpha: float = 1.0,
      bandit_prior_beta: float = 1.0,
      time_window: pd.Timedelta | None = None,  # None = all history
  ) -> dict

实现要求:
1. 输入验证 (required cols)
2. time_window 给定时, 仅用最近窗口数据
3. 对 group_by 每个 group:
   - 调用 oskill.calibration_analysis
   - include_bandit_state=True 时调用 oprim.bayes_beta_update
4. overall: 对全部数据再跑一次

返回 schema:
  {
    "overall": dict,  # calibration_analysis output
    "per_group": dict[str, dict],
    # 每个 group:
    # {calibration: dict, bandit_state: {alpha, beta, mean, ci, n_observed}}
    "summary": {
      "n_alerts_total": int,
      "n_groups": int,
      "best_calibrated_group": str,
      "worst_calibrated_group": str,
      "time_window_used": pd.Timedelta | None,
    },
    "warnings": list[str],
  }

测试要求 (≥ 11 用例):
1. 简单 100 alerts 单 group: overall + per_group (1 项)
2. 多 alert_types (3 个): per_group 含 3 项
3. include_bandit_state=False: bandit_state 缺失
4. time_window 测试 (仅近 30 天)
5. predictions 范围外 raise
6. outcomes 非 0/1 raise
7. n_alerts < 10 raise warning
8. **集成测试**: 真实 SPY 上下涨跌 alert 模拟, 端到端
9. **集成测试**: 不 mock
10. best/worst_calibrated_group 逻辑验证 (依据 ECE)
11. bandit_state.alpha + bandit_state.beta = prior + n_observed
12. 性能: 10000 alerts, 5 groups: < 2s

性能要求:
- 10000 alerts, 5 groups: < 2s

文件位置:
- 实现: omodul/signals.py
- 测试: tests/test_signals.py
- 文档: docs/signals.md
```

---

## Prompt 4.2: `features.thesis_invalidation_monitor`

```
[复用全局 Header]

任务: 实现 omodul.thesis_invalidation_monitor

业务用途: thesis 失效监控 (基于 Brier Score + 阈值 + 趋势)

调用的 oskill + ops (必须用):
  - oskill.calibration_analysis
  - oprim.mann_kendall_trend           (Brier 趋势)
  - oprim.brier_score_decomposed

数学逻辑:

  Thesis Invalidation 核心:
    1. 输入: thesis predictions (持续) + 实际 outcomes
    2. Rolling Brier Score (rolling window)
    3. Mann-Kendall trend on rolling Brier
    4. 失效判断:
       - Brier > brier_threshold (绝对失效)
       - Brier 趋势上升显著 (相对失效)
       - Reliability 持续 > resolution (反向校准)
    5. 输出失效状态 + 早期预警

API 签名:
  def thesis_invalidation_monitor(
      thesis_history: pd.DataFrame,
      # required cols: thesis_id, ts, predicted_prob, actual_outcome
      *,
      rolling_window: int = 30,
      brier_threshold: float = 0.25,
      include_trend_analysis: bool = True,
      mk_alpha: float = 0.05,
      group_by: str = "thesis_id",
  ) -> dict

实现要求:
1. 输入验证
2. group_by 维度分组
3. 每组:
   - Rolling Brier (rolling apply 调 oprim.brier_score_decomposed)
   - Trend test on rolling Brier (oprim.mann_kendall_trend)
   - 整体 calibration (oskill.calibration_analysis)
4. 失效判断:
   - latest_brier > brier_threshold AND trend_increasing → INVALIDATED
   - latest_brier > brier_threshold AND trend_stable → AT_RISK
   - latest_brier ≤ brier_threshold AND trend_increasing → WARNING
   - else → VALID

返回 schema:
  {
    "per_thesis": dict[str, dict],
    # 每个 thesis_id:
    # {
    #   status: Literal["VALID", "WARNING", "AT_RISK", "INVALIDATED"],
    #   latest_brier: float,
    #   rolling_brier: pd.Series,
    #   trend_test: dict,
    #   calibration: dict,
    #   alert_message: str,
    # }
    "summary": {
      "n_thesis": int,
      "n_valid": int, "n_warning": int, "n_at_risk": int, "n_invalidated": int,
      "invalidated_thesis_ids": list[str],
    },
    "warnings": list[str],
  }

测试要求 (≥ 12 用例):
1. 健康 thesis (Brier 稳定 < threshold): status=VALID
2. 趋势上升 + Brier < threshold: status=WARNING
3. Brier > threshold + 趋势平: status=AT_RISK
4. Brier > threshold + 趋势上升: status=INVALIDATED
5. 多 thesis 测试: per_thesis 多项
6. rolling_window > n raise
7. predictions 范围外 raise
8. outcomes 非 0/1 raise
9. **集成测试**: 真实数据, 模拟 thesis 衰退
10. **集成测试**: 不 mock
11. summary 数字加起来 = n_thesis
12. invalidated_thesis_ids 与 per_thesis status=INVALIDATED 一致
13. 性能: 10 thesis × 1000 obs: < 5s

性能要求:
- 10 thesis × 1000 obs, rolling=30: < 5s

文件位置: 同上 (signals.py)
```

---

# 组 5：Scenario / 风险管理（Scenario & Risk）—— 2 个

---

## Prompt 5.1: `features.scenario_stress_test`

```
[复用全局 Header]

任务: 实现 omodul.scenario_stress_test

业务用途: 完整 scenario stress test (历史类比 + 自定义 + Monte Carlo)

调用的 oskill + ops (必须用):
  - oskill.historical_analogy_search   (找历史类比 scenario)
  - oskill.regime_aware_performance    (per-scenario stats)
  - oskill.bootstrap_distribution      (不确定性)
  - oprim.value_at_risk
  - oprim.drawdown_curve
  - oprim.cumulative_returns

数学逻辑:

  Scenario Stress Test 核心:
    1. 输入: portfolio (returns 或 weights × returns) + scenarios
    2. 三类 scenarios:
       a. Historical: 历史日期范围 (e.g. "2008 financial crisis")
       b. Custom shock: 用户定义 (e.g. "all stocks -20% in 5 days")
       c. Analogy: 用 historical_analogy_search 找相似时期
    3. 对每个 scenario:
       - 模拟 portfolio 表现
       - 计算 VaR / ES / max drawdown
       - bootstrap CI
    4. Scenario 之间对比

API 签名:
  def scenario_stress_test(
      portfolio_returns: pd.Series | pd.DataFrame,
      historical_data: pd.DataFrame,
      *,
      scenarios: list[dict],
      # 每个 scenario:
      # {"name": str, "type": "historical|custom|analogy", "config": dict}
      bootstrap_ci: bool = True,
      n_bootstrap: int = 1000,
      var_confidence: float = 0.95,
  ) -> dict

scenarios config 例子:
  Historical: {"start": "2008-09-01", "end": "2009-03-31"}
  Custom shock: {"shock_pct": -0.20, "duration_days": 5}
  Analogy: {"current_panel": pd.DataFrame, "top_k": 5}

实现要求:
1. 输入验证
2. 对每个 scenario:
   - "historical": 提取 historical_data 中对应日期段, 应用到 portfolio
   - "custom": 模拟 shock 的 returns 序列
   - "analogy": 调用 oskill.historical_analogy_search 找相似
3. 每个 scenario 计算:
   - cumulative returns (oprim.cumulative_returns)
   - max drawdown (oprim.drawdown_curve)
   - VaR / ES (oprim.value_at_risk)
4. bootstrap_ci=True 时给关键指标 CI

返回 schema:
  {
    "per_scenario": list[dict],
    # 每个: {name, type, performance: {cum_return, max_dd, var, es, ...}, ci: {...}}
    "comparison": pd.DataFrame,  # rows=scenarios, cols=metrics
    "worst_case_scenario": str,
    "summary": dict,
  }

测试要求 (≥ 12 用例):
1. 单 scenario "historical"
2. 单 scenario "custom"
3. 单 scenario "analogy"
4. 多 scenario mix
5. 空 scenarios raise
6. 非法 scenario type raise
7. **集成测试**: 真实 SPY portfolio + 2008/2020 historical scenarios
8. **集成测试**: 不 mock historical_analogy_search
9. worst_case_scenario 逻辑验证
10. comparison DataFrame schema
11. bootstrap_ci=True 测试
12. 性能: 5 scenarios, 全部 metrics: < 30s

性能要求:
- 5 scenarios, n=252, n_bootstrap=1000: < 30s

文件位置:
- 实现: omodul/risk.py
- 测试: tests/test_risk.py
- 文档: docs/risk.md
```

---

## Prompt 5.2: `features.tail_risk_analyzer`

```
[复用全局 Header]

任务: 实现 omodul.tail_risk_analyzer

业务用途: 尾部风险完整分析 (parametric/historical/Cornish-Fisher 多方法 VaR + ES + tail metrics)

调用的 oskill + ops (必须用):
  - oprim.value_at_risk                (atomic VaR, 多方法)
  - oprim.skew_kurt_robust
  - oprim.kolmogorov_smirnov_test      (尾部分布拟合检验)
  - oskill.bootstrap_distribution

数学逻辑:

  Tail Risk Analysis 核心:
    1. 三方法 VaR/ES 对比:
       - historical (empirical quantile)
       - parametric (Gaussian)
       - cornish_fisher (skew/kurt 调整)
    2. Tail metrics:
       - skewness, kurtosis (oprim.skew_kurt_robust)
       - VaR/ES @ 95%, 99%
       - Tail dependency (如多资产)
    3. Distribution fit goodness (KS test vs normal)
    4. Bootstrap CI for VaR/ES estimates

  注意: 完整 GARCH-EVT-VaR 需要 oprim 添加 garch_fit + gpd_fit
        本 feature 不实现 EVT pipeline (仅 atomic VaR)
        如需 EVT: 用户在 omodul.tail_risk_evt (未来 feature)

API 签名:
  def tail_risk_analyzer(
      returns: pd.Series,
      *,
      confidence_levels: list[float] = [0.95, 0.99],
      methods: list[str] = ["historical", "parametric", "cornish_fisher"],
      bootstrap_ci: bool = True,
      n_bootstrap: int = 1000,
      include_normality_test: bool = True,
  ) -> dict

实现要求:
1. 对每个 confidence_level 和每个 method 调 oprim.value_at_risk
2. oprim.skew_kurt_robust 计算 skew/kurt
3. include_normality_test=True 时调 oprim.kolmogorov_smirnov_test (vs normal)
4. bootstrap_ci=True 时:
   - 每个 method × confidence_level 的 VaR estimate 用 oskill.bootstrap_distribution

返回 schema:
  {
    "var_es_table": pd.DataFrame,
    # rows=methods, cols=metrics × confidence_level
    "tail_metrics": {
      "skewness": float,
      "excess_kurtosis": float,
      "max_loss": float,
      "n_extreme_observations": int,
    },
    "normality_test": dict | None,
    "method_comparison": {
      "most_conservative": str,
      "most_liberal": str,
      "discrepancy_warning": bool,
    },
    "ci_per_var_estimate": dict | None,
    "summary": dict,
  }

测试要求 (≥ 11 用例):
1. 正态 returns: 三方法 VaR 接近
2. 重尾 returns: cornish_fisher VaR > parametric
3. 偏左 returns: cornish_fisher VaR > parametric
4. confidence_levels=[0.95, 0.99] 测试
5. methods 选择测试
6. include_normality_test=False
7. bootstrap_ci=False
8. n=30 raise warning
9. **集成测试**: 真实 SPY 历史 returns
10. **集成测试**: 不 mock
11. method_comparison 逻辑验证
12. 性能: n=10000, all methods, bootstrap=1000: < 5s

性能要求:
- n=10000, all methods, bootstrap=1000: < 5s

文件位置: 同上 (risk.py)
```

---

# 组 6：数据质量与诊断（Data Quality & Diagnostics）—— 2 个

---

## Prompt 6.1: `features.panel_data_quality_check`

```
[复用全局 Header]

任务: 实现 omodul.panel_data_quality_check

业务用途: panel data 完整 quality check (gap + outlier + freshness + consistency)

调用的 oskill + ops (必须用):
  - oskill.detect_outliers_robust
  - oskill.distribution_shift_test    (vs 历史 baseline)
  - oprim.gap_detect
  - oprim.lag_forward_fill              (检测 stale data)

数学逻辑:

  Panel Quality Check 核心:
    1. 输入: panel DataFrame (timestamp index, columns = fields)
    2. 对每列字段:
       - Gap detection (oprim.gap_detect)
       - Outlier detection (oskill.detect_outliers_robust)
       - Freshness check (last update timestamp)
       - Distribution drift vs baseline (如有 baseline)
    3. 整体 panel score:
       - 0-1, 1=完美
       - 加权: gap × 0.3 + outlier × 0.3 + freshness × 0.2 + drift × 0.2

API 签名:
  def panel_data_quality_check(
      panel: pd.DataFrame,
      *,
      expected_freq: str = "1D",
      baseline_panel: pd.DataFrame | None = None,
      check_freshness: bool = True,
      max_acceptable_gap_periods: int = 3,
      outlier_threshold_zscore: float = 3.0,
      now_timestamp: pd.Timestamp | None = None,
      score_weights: dict | None = None,
  ) -> dict

实现要求:
1. 对每列调用 oprim.gap_detect
2. 对每列调用 oskill.detect_outliers_robust
3. baseline_panel 给定 → 对每列 distribution_shift_test
4. check_freshness=True → 对每列计算 last update vs now_timestamp
5. 整体 score 加权
6. 输出每列 + overall report

返回 schema:
  {
    "per_field": dict[str, dict],
    # 每个 column:
    # {gaps: dict, outliers: dict, freshness: dict, drift: dict | None, field_score: float}
    "overall_score": float,
    "issues_summary": {
      "fields_with_gaps": list[str],
      "fields_with_outliers": list[str],
      "stale_fields": list[str],
      "fields_with_drift": list[str],
    },
    "panel_metadata": {
      "n_rows": int, "n_columns": int,
      "first_ts": pd.Timestamp, "last_ts": pd.Timestamp,
    },
    "warnings": list[str],
  }

测试要求 (≥ 11 用例):
1. 完美 panel: overall_score 接近 1
2. 含 gaps panel: overall_score < 1, gaps 字段非空
3. 含 outliers: outliers 字段非空
4. 旧数据 (last_update 远): stale_fields 非空
5. baseline_panel 给定: drift 字段非 None
6. check_freshness=False: freshness 字段缺失
7. 空 panel raise
8. **集成测试**: 真实 panel 数据 (e.g. 多资产 OHLCV)
9. **集成测试**: 不 mock
10. score_weights 自定义测试
11. 性能: panel 100 columns × 1000 rows: < 3s

性能要求:
- 100 cols × 1000 rows: < 3s

文件位置:
- 实现: omodul/data_quality.py
- 测试: tests/test_data_quality.py
- 文档: docs/data_quality.md
```

---

## Prompt 6.2: `features.cross_source_consistency_check`

```
[复用全局 Header]

任务: 实现 omodul.cross_source_consistency_check

业务用途: 多数据源一致性检查 (同一指标在不同 source 对比 + 漂移检测)

调用的 oskill + ops (必须用):
  - oskill.distribution_shift_test
  - oskill.detect_outliers_robust
  - oprim.pearson_spearman_corr

数学逻辑:

  Cross-Source Consistency 核心:
    1. 输入: 多 source 的同名 series (DataFrame: cols=source 名)
    2. 对每对 source:
       - Correlation (Pearson + Spearman)
       - Distribution shift test
       - Pointwise difference outlier detection
    3. 推荐"最可信" source (基于内部一致性)

API 签名:
  def cross_source_consistency_check(
      multi_source_data: pd.DataFrame,
      # cols = source names, all should be the "same" indicator
      *,
      reference_source: str | None = None,
      consistency_threshold_corr: float = 0.95,
      include_outlier_detection: bool = True,
      shift_methods: list = ["ks", "wasserstein"],
  ) -> dict

实现要求:
1. 输入验证 (≥ 2 列)
2. 对每对 (source_i, source_j):
   - oprim.pearson_spearman_corr
   - oskill.distribution_shift_test
3. 每对差异 series (source_i - source_j) 用 detect_outliers_robust
4. 一致性 score per source: 平均与其它 source 的相关性
5. 推荐 source = 一致性 score 最高的 (除非给定 reference_source)

返回 schema:
  {
    "pairwise_correlation": pd.DataFrame,  # symmetric
    "pairwise_shift": pd.DataFrame,        # bool
    "outlier_periods": dict[str, list],     # source pair → outlier timestamps
    "consistency_scores": dict[str, float],
    "recommended_source": str,
    "summary": {
      "n_sources": int,
      "all_consistent": bool,
      "lowest_correlation_pair": tuple[str, str],
    },
    "warnings": list[str],
  }

测试要求 (≥ 11 用例):
1. 完全一致两 source: 相关性 ≈ 1, all_consistent=True
2. 完全不同两 source: 相关性低, all_consistent=False
3. 三 source (其中两个一致, 一个 outlier source)
4. reference_source 指定: recommended_source = reference
5. include_outlier_detection=False
6. 仅 1 列 raise
7. **集成测试**: 真实多源数据 (e.g. CoinGecko vs CryptoCompare 同一币种 price)
8. **集成测试**: 不 mock
9. pairwise matrix 对称性
10. consistency_scores 加起来 / n = 平均相关性
11. 性能: 5 sources × 10000 rows: < 3s

性能要求:
- 5 sources × 10000 rows: < 3s

文件位置: 同上 (data_quality.py)
```

---

# 组 7：相似度与检索（Similarity & Search）—— 2 个

---

## Prompt 7.1: `features.smart_peer_finder`

```
[复用全局 Header]

任务: 实现 omodul.smart_peer_finder

业务用途: 智能同业检索 (找历史/当前最相似的资产或时段)

调用的 oskill + ops (必须用):
  - oskill.historical_analogy_search
  - oprim.cosine_similarity_batch
  - oprim.euclidean_distance_matrix
  - oprim.percentile_rank

数学逻辑:

  Smart Peer Finder 核心:
    1. 输入: query asset (panel signature) + candidate pool
    2. 多维相似度:
       - signature similarity (cosine)
       - temporal similarity (DTW)
       - distribution similarity (Wasserstein)
    3. Ensemble ranking (调用 historical_analogy_search 内部 ensemble)
    4. 输出 top-K + 解释 (每个 candidate 在每维度的 rank)

API 签名:
  def smart_peer_finder(
      query: dict,
      # {"signature": np.ndarray, "timeseries": pd.DataFrame | None}
      candidates: list[dict],
      # 每个 candidate: {"id": str, "signature": np.ndarray, "timeseries": pd.DataFrame | None}
      *,
      methods: list = ["cosine", "dtw", "wasserstein"],
      ensemble: Literal["mean_rank", "borda", "weighted"] = "mean_rank",
      weights: dict | None = None,
      top_k: int = 10,
      include_explanation: bool = True,
  ) -> dict

实现要求:
1. 输入验证: query / candidates 都有 "signature"
2. 对每个 method:
   - "cosine": cosine_similarity_batch (signature based)
   - "dtw" / "wasserstein": 调 historical_analogy_search (timeseries based)
3. ensemble (调 historical_analogy_search 的 ensemble 逻辑或自实现)
4. 每个 top-k candidate, 给"为什么相似"解释:
   - 每维度 rank
   - 主要相似维度

返回 schema:
  {
    "matches": list[dict],
    # 每个: {rank, candidate_id, ensemble_score, methods_scores: dict, explanation: str}
    "summary": {
      "n_candidates": int,
      "methods_used": list[str],
      "primary_similarity_dimension": str,
    },
    "warnings": list[str],
  }

测试要求 (≥ 11 用例):
1. query 与 candidates[0] 完全相同: candidate[0] 是 top 1
2. methods=["cosine"] 仅一个
3. methods 全部
4. ensemble="weighted" + weights
5. weights 不给 ensemble="weighted" raise
6. top_k > len(candidates) 调整
7. timeseries=None 时, dtw/wasserstein 不可用 (raise warning, 仅用 cosine)
8. **集成测试**: 真实股票数据 (5 大科技股), 找 AAPL 的 peer
9. **集成测试**: 不 mock
10. include_explanation=True 测试
11. 性能: 100 candidates, methods=["cosine", "dtw"]: < 30s

性能要求:
- 100 candidates, all methods, top_k=10: < 30s

文件位置:
- 实现: omodul/similarity.py
- 测试: tests/test_similarity.py
- 文档: docs/similarity.md
```

---

## Prompt 7.2: `features.event_cascade_clusterer`

```
[复用全局 Header]

任务: 实现 omodul.event_cascade_clusterer

业务用途: 事件级联聚类 (相关 news events → cluster + 影响范围)

调用的 oskill + ops (必须用):
  - oprim.cosine_similarity_batch  (embedding 相似度)
  - oskill.detect_outliers_robust (异常事件)
  - sklearn.cluster.DBSCAN  (Layer 3 允许直接用 sklearn 对于聚类)

数学逻辑:

  Event Cascade Clustering 核心:
    1. 输入: events DataFrame (含 ts, embedding, content)
    2. Cosine similarity matrix (oprim.cosine_similarity_batch)
    3. DBSCAN 聚类 (基于 1 - cosine_similarity 作为距离)
    4. 对每个 cluster:
       - 时间范围 (first_ts, last_ts)
       - 中心 event (与其它最相似)
       - 异常事件 (oskill.detect_outliers_robust)
    5. 输出 cluster 分组 + 描述

API 签名:
  def event_cascade_clusterer(
      events: pd.DataFrame,
      # required cols: event_id, timestamp, embedding (np.ndarray)
      *,
      eps: float = 0.3,  # DBSCAN distance threshold
      min_samples: int = 3,
      time_window_hours: float | None = None,  # 可选: 限制 cluster 时间范围
      include_outlier_detection: bool = True,
  ) -> dict

实现要求:
1. 输入验证
2. 计算 distance matrix = 1 - cosine_similarity_batch (embeddings)
3. 应用 DBSCAN (metric='precomputed')
4. 对每个 cluster:
   - cluster_id, member event_ids
   - first_ts, last_ts, span
   - centroid event (highest avg similarity to cluster)
5. include_outlier_detection: detect_outliers_robust 找 noise event 中的极端
6. time_window_hours: filter cluster spanning longer

返回 schema:
  {
    "clusters": list[dict],
    # 每个 cluster:
    # {
    #   cluster_id: int,
    #   member_event_ids: list, n_members: int,
    #   first_ts: pd.Timestamp, last_ts: pd.Timestamp,
    #   span_hours: float,
    #   centroid_event_id: str,
    # }
    "noise_events": list,  # event_ids
    "outlier_events": list | None,  # if include_outlier_detection
    "summary": {
      "n_events_total": int,
      "n_clusters": int,
      "n_noise": int,
      "largest_cluster_size": int,
    },
  }

测试要求 (≥ 11 用例):
1. 简单 10 events, 3 个明显 cluster: 输出 3 clusters
2. 全部 events 太分散: clusters=[], noise=all
3. eps 不同测试 (大 eps → 少 cluster)
4. min_samples=5 测试
5. time_window_hours 限制
6. embedding 维度不一致 raise
7. events < min_samples raise
8. **集成测试**: 真实 news embeddings (e.g. sentence-transformers 输出)
9. **集成测试**: 不 mock
10. centroid 真是 cluster 内部最相似
11. 性能: 1000 events, embedding dim=384: < 30s

性能要求:
- 1000 events, embedding dim=384: < 30s

文件位置: 同上 (similarity.py)
```

---

# 组 8：报告与导出（Report & Export）—— 2 个 ⚠️ 待重新评估

**重要警告**：以下 2 个元模块在 Omodul-Layer3-Proposal.md §6 中标注为"待重新评估"——可能违反 Layer 3 定义。CC 实施前必须**先与 Wiki 确认**。

---

## Prompt 8.1: `features.standardized_performance_report` ⚠️

```
[复用全局 Header]

⚠️ 警告 ⚠️
此元模块在 Layer 3 候选清单中标注为"待重新评估", 可能违反 Layer 3 三大纪律 (内部不互相调用其它 Layer 3)。

实施前必须确认:
1. 是否真不调用其它 features.*?
2. 是否仅是"业务模块层"而非 Layer 3?
3. Wiki 是否同意以"重新设计避免调 features"方式实施?

如果 Wiki 不确认, 暂不实施。

======================================================================

任务: 实现 omodul.standardized_performance_report

业务用途: 标准化性能报告 (HTML/JSON/Markdown 三格式)

修订设计 (避免调用其它 features):
  本元模块仅调用 oskill + oprim, 不调其它 omodul
  这相当于 features.strategy_backtest_report 的"渲染"专用版本

调用的 oskill + ops:
  与 features.strategy_backtest_report 类似 (重复, 不调它)

API 签名:
  def standardized_performance_report(
      returns: pd.Series,
      *,
      output_format: Literal["json", "markdown", "html"] = "markdown",
      include_charts_data: bool = True,
      regime_labels: pd.Series | None = None,
      benchmark_returns: pd.Series | None = None,
  ) -> str | dict

实现要求:
1. ⚠️ 内部不能调 features.strategy_backtest_report
2. 直接组合 oskill.bootstrap_sharpe / psr_dsr / regime_aware_performance
3. 渲染成请求 format

测试要求 (≥ 9 用例):
1. JSON output schema
2. Markdown output 含必要 sections
3. HTML output 含 chart placeholders
4. include_charts_data=True 时 data 字段非空
5. regime_labels 给定测试
6. **集成测试**: 不 mock
7. 三种 format 数据一致 (仅渲染不同)
8. 性能: 252 obs: < 5s

⚠️ 实施提示:
  与 features.strategy_backtest_report 重复度高
  Wiki 可能决定本 feature 应该合并到 strategy_backtest_report 的 report_format 参数
  或者本 feature 应该归 helios-tools (不是 features)
  实施前先确认

文件位置:
- 实现: omodul/reports.py (如确认实施)
- 测试: tests/test_reports.py
- 文档: docs/reports.md
```

---

## Prompt 8.2: `features.tradingview_signal_export` ⚠️

```
[复用全局 Header]

⚠️ 警告 ⚠️
此元模块在 Layer 3 候选清单中标注为"待重新评估"。
- 几乎不调用 oprim/skills (主要是文本生成)
- 可能违反 Layer 3 "必须用 oprim/skills 实现核心" 纪律
- 可能更适合归入 helios-tools 而非 omodul

实施前必须确认:
1. 此元模块是否应该归入 helios-tools 而非 omodul?
2. Wiki 是否同意以当前形式实施?

如果 Wiki 不确认, 暂不实施。

======================================================================

任务: 实现 omodul.tradingview_signal_export

业务用途: 把 strategy signals 导出为 TradingView Pine Script v6 格式

调用的 oskill + ops:
  几乎不调用 (主要是文本生成 + JSON serialization)

API 签名:
  def tradingview_signal_export(
      signals: pd.DataFrame,
      # required cols: timestamp, signal_value
      *,
      pine_version: int = 6,
      title: str = "Helios Signal",
      overlay: bool = True,
  ) -> str:  # Pine Script v6 代码

实现要求:
1. 把 signals DataFrame 转成 Pine Script:
   - 输出 var line/marker 代码
   - 时间戳转 timestamp 函数
   - signal_value 用 plot 函数
2. 不调用 oprim/skills (违反 Layer 3 纪律, 重新评估)

测试要求 (≥ 8 用例):
1. 简单 signals 输出 Pine Script
2. 验证 pine 版本 declaration
3. 验证 plot 代码生成
4. signals 缺 col raise
5. 时间戳转换正确
6. **集成测试**: 输出 Pine Script 在 TradingView 实际可粘贴运行 (人工验证)
7. 性能: 1000 signals: < 1s

⚠️ 实施提示:
  本元模块可能应该归 helios-tools 而非 omodul
  Wiki 可能决定本 feature 不实施, 或转入 helios-tools 包

文件位置 (如确认实施):
- 实现: omodul/exports.py
- 测试: tests/test_exports.py
- 文档: docs/exports.md
```

---

# 总结

```yaml
total_prompts: 16
组 1 交易行为:        2 (trade_journal_analyzer / shadow_account_simulator)
组 2 Regime / 市场:    3 (regime_replay_search / regime_change_detector / regime_dashboard)
组 3 策略验证:        3 (strategy_backtest_report / strategy_decay_monitor / factor_attribution_report)
组 4 信号告警:        2 (alert_calibration_engine / thesis_invalidation_monitor)
组 5 Scenario / 风险: 2 (scenario_stress_test / tail_risk_analyzer)
组 6 数据质量:        2 (panel_data_quality_check / cross_source_consistency_check)
组 7 相似度:          2 (smart_peer_finder / event_cascade_clusterer)
组 8 报告导出 ⚠️:     2 (standardized_performance_report / tradingview_signal_export)

three_layer_3_disciplines_summary:

  discipline_1_no_internal_calls:
    omodul 内部不允许 import omodul.*
    PR lint check 自动阻断

  discipline_2_must_use_layer_1_2:
    必须 import oskill.* 和/或 oprim.*
    14 个元模块严格遵守
    2 个 ⚠️ 待重新评估的可能违反 (Wiki 决定后再实施)

  discipline_3_real_world_dogfood:
    每个元模块必须有真实业务数据测试
    比 Layer 1/2 的单元测试 + mock 严格

usage:
  - 14 个非 ⚠️ 元模块可立即分发给 CC
  - 2 个 ⚠️ 元模块必须先与 Wiki 确认设计是否合规
  - 推荐多 CC instance 并行 (3-5 个)
  - 每个 prompt 跑 5-10 天完成 (Layer 3 业务复杂度)
  - 总周期: 5-7 周 (并行)

prerequisites:
  oprim 1.0 已 ship
  oskill 1.0 已 ship
  31 ops + 13 skills 全部可调用

next_steps:
  1. Wiki 创建 omodul 仓库基础结构
  2. 设置 CI/CD (lint + test + coverage gate + Layer 3 纪律 lint)
  3. 决定 8.1 / 8.2 实施方式 (待重新评估 features)
  4. 分发 14 个 prompts 给 CC
  5. 每完成 1 PR Wiki review + merge
  6. 收集真实 dogfood 反馈
  7. omodul 0.1 (候选实施版) → 1.0 (正式版) 升级
```

---

## Layer 3 纪律提醒（每个 prompt 都强调）

```yaml
layer_3_disciplines:

  discipline_1_no_internal_calls:
    omodul/*.py 不允许 import omodul.*
    PR lint check 强制

  discipline_2_must_use_lower_layers:
    必须 import oskill.* 和/或 oprim.*
    至少一处实质性调用
    PR review 关注点

  discipline_3_real_world_dogfood:
    单元测试 + mock 不够
    必须真实业务数据 (e.g. SPY / BTC 历史 returns) 端到端测试

  discipline_4_neutral_design:
    输入用通用 schema (DataFrame / dict)
    输出用通用结果 (dict / DataFrame)
    不绑定特定项目存储 / 展示
```

---

**END OF 16 CC IMPLEMENTATION PROMPTS**
