"""omodul.strategy sub-package — re-exports strategy.py + Sprint 0 elements."""

from __future__ import annotations

import importlib.util
import pathlib

# Load strategy.py (sibling file shadowed by this package) via importlib
_strategy_py = pathlib.Path(__file__).parent.parent / "strategy.py"
_spec = importlib.util.spec_from_file_location("omodul._strategy_legacy", _strategy_py)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

strategy_backtest_report = _mod.strategy_backtest_report
strategy_decay_monitor = _mod.strategy_decay_monitor
factor_attribution_report = _mod.factor_attribution_report

from omodul.strategy.daily_plan_generator import daily_plan_generate

__all__ = [
    "strategy_backtest_report",
    "strategy_decay_monitor",
    "factor_attribution_report",
    "daily_plan_generate",
]
