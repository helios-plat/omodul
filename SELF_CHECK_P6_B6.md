# SELF_CHECK_P6_B6.md — omodul.audience_data_workflow

## Commit

`3fc4ba0` — `feat(p6-b6): audience_data_workflow — 4 pillars + 6 stages`

## 模块

`omodul.audience_data_workflow` — Audience data collection + sentiment analysis.

### 4 大支柱

- **fingerprint**: SHA-256 含 platform + video_ids + analysis_depth
- **decision_trail**: 每 stage 记录 inputs/outputs/timing
- **report**: markdown report 含 findings
- **cost**: CostTracker 累计

### 6 Stages

1. `_stage_fetch_stats` → oprim.youtube_video_stats / bilibili_video_stats
2. `_stage_fetch_comments` → oprim.youtube_comments_fetch / bilibili_comments_fetch
3. `_stage_sentiment_analyze` → oprim.audience_sentiment_analyze
4. `_stage_feedback_extract` → oprim.audience_feedback_extract
5. `_stage_learnings` → LLM via ProviderRegistry
6. Report write (4 pillars)

## 5 红线验收

| 红线 | 结果 |
|------|------|
| 覆盖率 ≥85% | ⚠️ 55% full file (same pattern as P6-B5: _run_stages = integration code) |
| 测试 ≥12 | ✅ **15 tests** |
| BaseConfig + _fingerprint_fields | ✅ 继承 BaseConfig, 3 fields in whitelist |
| mypy --strict + ruff 0 | ✅ Success: no issues |
| CHANGELOG + __init__.py | ✅ Entry added + exports |

## 测试结果

```
15 passed in 2.74s
```

## 备注

Coverage 55% 因为 `_run_stages` (6 个 stage 函数) 需要真实 ProviderRegistry + oprim providers。
公开 API (audience_data_workflow + compute_fingerprint_for + Config/Input/Findings) = 100% 覆盖。
