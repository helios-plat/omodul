# omodul

End-to-end financial analysis features built on [oprim](https://github.com/helios-plat/oprim) + [oskill](https://github.com/helios-plat/oskill).

> ⚠️ **v0.1.0 is a candidate release.** API may change before 1.0. Requires dogfood validation from ≥2 projects before 1.0 promotion.

## Installation

```bash
pip install omodul
```

## Architecture

```
omodul (Layer 3) → oskill (Layer 2) → oprim (Layer 1) → numpy/scipy/pandas (Layer 0)
```

## Modules (14)

| Group | Modules |
|-------|---------|
| Trading Behavior | `trade_journal_analyzer`, `shadow_account_simulator` |
| Regime | `regime_replay_search`, `regime_change_detector`, `regime_conditional_dashboard_data` |
| Strategy | `strategy_backtest_report`, `strategy_decay_monitor`, `factor_attribution_report` |
| Signals | `alert_calibration_engine`, `thesis_invalidation_monitor` |
| Risk | `scenario_stress_test`, `tail_risk_analyzer` |
| Data Quality | `panel_data_quality_check`, `cross_source_consistency_check` |
| Similarity | `smart_peer_finder`, `event_cascade_clusterer` |

## License

MIT
