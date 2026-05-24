from contextvars import ContextVar

from obase.cost_tracker import CostTracker

_current_cost_tracker: ContextVar[CostTracker | None] = ContextVar(
    "cost_tracker", default=None
)
