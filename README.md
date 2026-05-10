# omodul

End-to-end financial analysis features built on [oprim](https://github.com/helios-plat/oprim) + [oskill](https://github.com/helios-plat/oskill).

> ⚠️ **v0.1.0 is a candidate release**, not a 1.0 stable release.
> - API may change in 0.x versions
> - 2 modules (`standardized_performance_report`, `tradingview_signal_export`) are deferred pending design review
> - **1.0 promotion requires**: ≥2 projects dogfood for ≥3 months + deferred module decisions finalized

## Installation

```bash
pip install omodul
```

## Architecture

```
omodul (Layer 3) → oskill (Layer 2) → oprim (Layer 1) → numpy/scipy/pandas (Layer 0)
```

## Modules (16 implemented)

| Group | Modules |
|-------|---------|
| Trading Behavior | `trade_journal_analyzer`, `shadow_account_simulator` |
| Regime | `regime_replay_search`, `regime_change_detector`, `regime_conditional_dashboard_data` |
| Strategy | `strategy_backtest_report`, `strategy_decay_monitor`, `factor_attribution_report` |
| Signals | `alert_calibration_engine`, `thesis_invalidation_monitor` |
| Risk | `scenario_stress_test`, `tail_risk_analyzer` |
| Data Quality | `panel_data_quality_check`, `cross_source_consistency_check` |
| Similarity | `smart_peer_finder`, `event_cascade_clusterer` |

### ⚠️ Deferred (not implemented in 0.1)

| Module | Reason |
|--------|--------|
| `standardized_performance_report` | High overlap with `strategy_backtest_report`; may merge or move to tools |
| `tradingview_signal_export` | Minimal oprim/oskill usage; may belong in helios-tools |

## License

MIT
