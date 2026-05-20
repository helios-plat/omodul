"""Scheduler-domain errors."""
from __future__ import annotations

from oprim.errors import StratumError


class SchedulerError(StratumError):
    """Generic scheduler failure."""


class JobNotFoundError(SchedulerError):
    """Requested scheduled job ID does not exist."""


class JobAlreadyExistsError(SchedulerError):
    """A job with this name already exists for the user."""
