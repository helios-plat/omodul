"""Canonical experiment loop element.

The engine owns experiment state and comparison semantics only.  It never
executes a physical trial itself: ``execute_trial`` must call an injected
``ExecutionPort``.  A decision is a candidate recommendation backed by
metrics; it is not a GoalRun or project acceptance verdict.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from obase.element_contract import ElementContract, zero_authority


class ExperimentError(Exception):
    """Base error for invalid experiment state or provider failures."""


class ExecutionPortUnavailable(ExperimentError):
    """Raised when a caller attempts to run without an injected execution port."""


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _id(prefix: str, value: Any) -> str:
    return f"{prefix}-{hashlib.sha256(_stable(value).encode()).hexdigest()[:16]}"


@dataclass(frozen=True, slots=True)
class Hypothesis:
    statement: str
    hypothesis_id: str = "hypothesis"
    assumptions: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.hypothesis_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "statement": self.statement,
            "assumptions": list(self.assumptions),
        }


@dataclass(frozen=True, slots=True)
class MetricSpec:
    name: str
    direction: str = "maximize"
    minimum_improvement: float = 0.0
    reproducible: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.direction not in {"maximize", "minimize"}:
            raise ValueError("metric direction must be 'maximize' or 'minimize'")
        if not self.name:
            raise ValueError("metric name must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "direction": self.direction,
            "minimum_improvement": self.minimum_improvement,
            "reproducible": self.reproducible,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Baseline:
    metrics: Mapping[str, float] = field(default_factory=dict)
    candidate_id: str = "baseline"
    revision: str | None = None

    def value(self, metric_name: str) -> float | None:
        raw = self.metrics.get(metric_name)
        return float(raw) if raw is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": dict(self.metrics),
            "candidate_id": self.candidate_id,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class Constraint:
    name: str
    operator: str = "allow"
    limit: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def check(self, value: float | None) -> bool:
        if self.operator == "allow" or self.limit is None:
            return True
        if value is None:
            return False
        if self.operator == "<=":
            return value <= self.limit
        if self.operator == ">=":
            return value >= self.limit
        if self.operator == "<":
            return value < self.limit
        if self.operator == ">":
            return value > self.limit
        if self.operator == "==":
            return value == self.limit
        raise ValueError(f"unsupported constraint operator: {self.operator}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "operator": self.operator,
            "limit": self.limit,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    description: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.candidate_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "description": self.description,
            "parameters": dict(self.parameters),
            "parent_id": self.parent_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    experiment_id: str
    hypothesis: Hypothesis | str
    metric: MetricSpec = field(default_factory=lambda: MetricSpec("score"))
    baseline: Baseline = field(default_factory=Baseline)
    constraints: tuple[Constraint, ...] = ()
    max_trials: int = 10
    max_wall_time: float | None = None
    max_cost: float | None = None
    max_failures: int = 3
    early_stop: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
    metric_direction: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.hypothesis, str):
            object.__setattr__(self, "hypothesis", Hypothesis(self.hypothesis))
        if self.metric_direction is not None:
            if self.metric_direction not in {"maximize", "minimize"}:
                raise ValueError("metric_direction must be 'maximize' or 'minimize'")
            if self.metric.direction != self.metric_direction:
                object.__setattr__(
                    self,
                    "metric",
                    MetricSpec(
                        name=self.metric.name,
                        direction=self.metric_direction,
                        minimum_improvement=self.metric.minimum_improvement,
                        reproducible=self.metric.reproducible,
                        metadata=self.metric.metadata,
                    ),
                )
        if self.max_trials < 1:
            raise ValueError("max_trials must be >= 1")
        if self.max_failures < 0:
            raise ValueError("max_failures must be >= 0")
        if not self.experiment_id:
            raise ValueError("experiment_id must be non-empty")

    @property
    def id(self) -> str:
        return self.experiment_id

    def to_dict(self) -> dict[str, Any]:
        hypothesis = self.hypothesis
        if isinstance(hypothesis, str):
            hypothesis = Hypothesis(hypothesis)
        return {
            "experiment_id": self.experiment_id,
            "hypothesis": hypothesis.to_dict(),
            "metric": self.metric.to_dict(),
            "metric_direction": self.metric.direction,
            "baseline": self.baseline.to_dict(),
            "constraints": [constraint.to_dict() for constraint in self.constraints],
            "max_trials": self.max_trials,
            "max_wall_time": self.max_wall_time,
            "max_cost": self.max_cost,
            "max_failures": self.max_failures,
            "early_stop": self.early_stop,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExperimentSpec:
        hypothesis_raw = data.get("hypothesis", "")
        hypothesis = (
            Hypothesis(
                str(hypothesis_raw.get("statement", "")),
                str(hypothesis_raw.get("hypothesis_id", "hypothesis")),
                tuple(str(item) for item in hypothesis_raw.get("assumptions", [])),
            )
            if isinstance(hypothesis_raw, Mapping)
            else str(hypothesis_raw)
        )
        metric_raw = data.get("metric", {})
        baseline_raw = data.get("baseline", {})
        return cls(
            experiment_id=str(data.get("experiment_id", data.get("id", ""))),
            hypothesis=hypothesis,
            metric=MetricSpec(
                name=str(metric_raw.get("name", "score")),
                direction=str(metric_raw.get("direction", "maximize")),
                minimum_improvement=float(metric_raw.get("minimum_improvement", 0.0)),
                reproducible=bool(metric_raw.get("reproducible", True)),
                metadata=dict(metric_raw.get("metadata", {})),
            ),
            metric_direction=(
                str(data["metric_direction"]) if data.get("metric_direction") is not None else None
            ),
            baseline=Baseline(
                metrics={str(k): float(v) for k, v in baseline_raw.get("metrics", {}).items()},
                candidate_id=str(baseline_raw.get("candidate_id", "baseline")),
                revision=baseline_raw.get("revision"),
            ),
            constraints=tuple(
                Constraint(
                    name=str(item.get("name", "constraint")),
                    operator=str(item.get("operator", "allow")),
                    limit=float(item["limit"]) if item.get("limit") is not None else None,
                    metadata=dict(item.get("metadata", {})),
                )
                for item in data.get("constraints", [])
            ),
            max_trials=int(data.get("max_trials", 10)),
            max_wall_time=(
                float(data["max_wall_time"]) if data.get("max_wall_time") is not None else None
            ),
            max_cost=float(data["max_cost"]) if data.get("max_cost") is not None else None,
            max_failures=int(data.get("max_failures", 3)),
            early_stop=bool(data.get("early_stop", False)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class TrialSpec:
    trial_id: str
    experiment_id: str
    candidate: Candidate
    iteration: int
    input: Mapping[str, Any] = field(default_factory=dict)
    constraints: tuple[Constraint, ...] = ()

    @property
    def id(self) -> str:
        return self.trial_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "experiment_id": self.experiment_id,
            "candidate": self.candidate.to_dict(),
            "iteration": self.iteration,
            "input": dict(self.input),
            "constraints": [constraint.to_dict() for constraint in self.constraints],
        }


@dataclass(frozen=True, slots=True)
class MetricResult:
    name: str
    value: float | None
    reproducible: bool = True
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def metric_name(self) -> str:
        return self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "reproducible": self.reproducible,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class TrialResult:
    trial_id: str
    candidate_id: str
    status: str = "completed"
    metrics: Mapping[str, float] = field(default_factory=dict)
    outputs: Mapping[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    wall_time_s: float = 0.0
    error: str | None = None
    evidence_refs: tuple[str, ...] = ()

    @property
    def failed(self) -> bool:
        return self.status.lower() in {"failed", "error", "cancelled", "timed_out"} or bool(
            self.error
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "metrics": dict(self.metrics),
            "outputs": dict(self.outputs),
            "cost_usd": self.cost_usd,
            "wall_time_s": self.wall_time_s,
            "error": self.error,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class Comparison:
    metric_name: str
    baseline_value: float | None
    candidate_value: float | None
    delta: float | None
    direction: str
    improved: bool
    sufficient_improvement: bool
    reproducible: bool = True
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def better(self) -> bool:
        return self.improved

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "baseline_value": self.baseline_value,
            "candidate_value": self.candidate_value,
            "delta": self.delta,
            "direction": self.direction,
            "improved": self.improved,
            "sufficient_improvement": self.sufficient_improvement,
            "reproducible": self.reproducible,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class ExperimentDecision:
    decision_id: str
    experiment_id: str
    candidate_id: str | None
    decision: str
    reason: str
    comparison: Comparison | None = None
    evidence_bundle: Mapping[str, Any] = field(default_factory=dict)
    authority: str = "candidate_recommendation"

    @property
    def accepted(self) -> bool:
        """Candidate recommendation only; never a project acceptance verdict."""

        return self.decision == "accept"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "experiment_id": self.experiment_id,
            "candidate_id": self.candidate_id,
            "decision": self.decision,
            "reason": self.reason,
            "comparison": self.comparison.to_dict() if self.comparison else None,
            "evidence_bundle": dict(self.evidence_bundle),
            "authority": self.authority,
        }


@dataclass(slots=True)
class ExperimentHistory:
    spec: ExperimentSpec
    trials: list[TrialResult] = field(default_factory=list)
    decisions: list[ExperimentDecision] = field(default_factory=list)
    proposed: list[TrialSpec] = field(default_factory=list)
    next_iteration: int = 1
    failures: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    schema_version: int = 1

    @property
    def experiment_id(self) -> str:
        return self.spec.experiment_id

    @property
    def cost_usd(self) -> float:
        return sum(result.cost_usd for result in self.trials)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "trials": [trial.to_dict() for trial in self.trials],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "proposed": [trial.to_dict() for trial in self.proposed],
            "next_iteration": self.next_iteration,
            "failures": self.failures,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "schema_version": self.schema_version,
        }


@runtime_checkable
class ExecutionPort(Protocol):
    async def execute(self, trial: TrialSpec) -> TrialResult | Mapping[str, Any]: ...


@runtime_checkable
class MetricPort(Protocol):
    async def evaluate(
        self,
        trial: TrialSpec | None,
        result: TrialResult,
        metric: MetricSpec,
    ) -> MetricResult | Mapping[str, Any]: ...


@runtime_checkable
class ExperimentHistoryStore(Protocol):
    async def save(self, history: ExperimentHistory) -> None: ...

    async def load(self, experiment_id: str) -> ExperimentHistory | None: ...


class MemoryExperimentHistoryStore:
    def __init__(self) -> None:
        self._histories: dict[str, ExperimentHistory] = {}

    async def save(self, history: ExperimentHistory) -> None:
        self._histories[history.experiment_id] = history

    async def load(self, experiment_id: str) -> ExperimentHistory | None:
        return self._histories.get(experiment_id)


class JsonExperimentHistoryStore:
    """Atomic JSON persistence provider with a versioned history payload."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, experiment_id: str) -> Path:
        safe = hashlib.sha256(experiment_id.encode("utf-8")).hexdigest()
        return self.root / f"{safe}.json"

    async def save(self, history: ExperimentHistory) -> None:
        path = self._path(history.experiment_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(_stable(history.to_dict()), encoding="utf-8")
        tmp.replace(path)

    async def load(self, experiment_id: str) -> ExperimentHistory | None:
        path = self._path(experiment_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return None
        return _history_from_dict(data)


def _candidate_from_dict(data: Mapping[str, Any]) -> Candidate:
    return Candidate(
        candidate_id=str(data.get("candidate_id", data.get("id", "candidate"))),
        description=str(data.get("description", "")),
        parameters=dict(data.get("parameters", {})),
        parent_id=data.get("parent_id"),
        metadata=dict(data.get("metadata", {})),
    )


def _constraints_from_dict(data: Sequence[Mapping[str, Any]]) -> tuple[Constraint, ...]:
    return tuple(
        Constraint(
            name=str(item.get("name", "constraint")),
            operator=str(item.get("operator", "allow")),
            limit=float(item["limit"]) if item.get("limit") is not None else None,
            metadata=dict(item.get("metadata", {})),
        )
        for item in data
    )


def _trial_from_dict(data: Mapping[str, Any]) -> TrialSpec:
    return TrialSpec(
        trial_id=str(data.get("trial_id", data.get("id", "trial"))),
        experiment_id=str(data.get("experiment_id", "")),
        candidate=_candidate_from_dict(data.get("candidate", {})),
        iteration=int(data.get("iteration", 1)),
        input=dict(data.get("input", {})),
        constraints=_constraints_from_dict(data.get("constraints", [])),
    )


def _result_from_dict(data: Mapping[str, Any]) -> TrialResult:
    return TrialResult(
        trial_id=str(data.get("trial_id", "")),
        candidate_id=str(data.get("candidate_id", "")),
        status=str(data.get("status", "completed")),
        metrics={str(k): float(v) for k, v in data.get("metrics", {}).items()},
        outputs=dict(data.get("outputs", {})),
        cost_usd=float(data.get("cost_usd", 0.0)),
        wall_time_s=float(data.get("wall_time_s", 0.0)),
        error=data.get("error"),
        evidence_refs=tuple(str(item) for item in data.get("evidence_refs", [])),
    )


def _comparison_from_dict(data: Mapping[str, Any]) -> Comparison:
    return Comparison(
        metric_name=str(data.get("metric_name", "score")),
        baseline_value=data.get("baseline_value"),
        candidate_value=data.get("candidate_value"),
        delta=data.get("delta"),
        direction=str(data.get("direction", "maximize")),
        improved=bool(data.get("improved", False)),
        sufficient_improvement=bool(data.get("sufficient_improvement", False)),
        reproducible=bool(data.get("reproducible", True)),
        details=dict(data.get("details", {})),
    )


def _decision_from_dict(data: Mapping[str, Any]) -> ExperimentDecision:
    comparison = data.get("comparison")
    return ExperimentDecision(
        decision_id=str(data.get("decision_id", "")),
        experiment_id=str(data.get("experiment_id", "")),
        candidate_id=data.get("candidate_id"),
        decision=str(data.get("decision", "reject")),
        reason=str(data.get("reason", "")),
        comparison=_comparison_from_dict(comparison) if isinstance(comparison, Mapping) else None,
        evidence_bundle=dict(data.get("evidence_bundle", {})),
        authority=str(data.get("authority", "candidate_recommendation")),
    )


def _history_from_dict(data: Mapping[str, Any]) -> ExperimentHistory:
    spec = ExperimentSpec.from_dict(data.get("spec", {}))
    return ExperimentHistory(
        spec=spec,
        trials=[_result_from_dict(item) for item in data.get("trials", [])],
        decisions=[_decision_from_dict(item) for item in data.get("decisions", [])],
        proposed=[_trial_from_dict(item) for item in data.get("proposed", [])],
        next_iteration=int(data.get("next_iteration", 1)),
        failures=int(data.get("failures", 0)),
        started_at=float(data.get("started_at", 0.0)),
        updated_at=float(data.get("updated_at", 0.0)),
        schema_version=int(data.get("schema_version", 1)),
    )


class ExperimentEngine:
    """Stateful experiment coordinator with all physical work injected."""

    ELEMENT_CONTRACT = ElementContract(
        element_id="experiment_engine",
        element_version=1,
        owner_repo="omodul",
        canonical_import="omodul.experiment_engine",
        canonical_export="ExperimentEngine",
        input_contract="ExperimentSpec, TrialSpec, TrialResult and metric evidence",
        output_contract="candidate recommendations, comparisons and persisted history",
        dependency_ports=("ExecutionPort", "MetricPort(optional)", "ExperimentHistoryStore"),
        state_model="versioned experiment history keyed by experiment_id",
        persistence_model="injected history store; atomic JSON or memory providers",
        authority_declaration=zero_authority(),
        async_contract="public lifecycle operations are awaitable; no private event loop",
        failure_semantics="failed/non-reproducible trials cannot produce an accept recommendation",
        recovery_semantics="history reload resumes trial count, budget and decision history",
        observability_contract="trial status, costs, errors and evidence refs are retained",
        compatibility_contract="decision is a candidate recommendation, never GoalRun acceptance",
        conformance_suite=(
            "BASELINE_STABLE",
            "TRIAL_ISOLATION",
            "METRIC_REPRODUCIBLE",
            "KEEP_REJECT_LOGIC",
            "FAILED_TRIAL_NOT_ACCEPTED",
            "BUDGET_ENFORCED",
            "RESUME_AFTER_RESTART",
            "EXPERIMENT_HISTORY_PERSISTED",
            "SECOND_EXECUTION_AUTHORITY",
            "SECOND_ACCEPTANCE_AUTHORITY",
        ),
    )

    def __init__(
        self,
        *,
        execution_port: ExecutionPort | None = None,
        metric_port: MetricPort | None = None,
        history_store: ExperimentHistoryStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.execution_port = execution_port
        self.metric_port = metric_port
        self.history_store = history_store or MemoryExperimentHistoryStore()
        self._clock = clock or time.time
        self._current_id: str | None = None
        self._histories: dict[str, ExperimentHistory] = {}

    async def create(self, spec: ExperimentSpec | Mapping[str, Any]) -> ExperimentHistory:
        """Create or idempotently load an experiment definition."""

        normalized = spec if isinstance(spec, ExperimentSpec) else ExperimentSpec.from_dict(spec)
        existing = await self._load(normalized.experiment_id)
        if existing is not None:
            self._current_id = existing.experiment_id
            return existing
        now = self._clock()
        history = ExperimentHistory(spec=normalized, started_at=now, updated_at=now)
        await self._save(history)
        return history

    async def propose_trial(
        self,
        candidate: Candidate | Mapping[str, Any] | None = None,
        *,
        experiment_id: str | None = None,
        input: Mapping[str, Any] | None = None,
    ) -> TrialSpec | None:
        history = await self._get(experiment_id)
        reason = self._budget_reason(history)
        if reason is not None:
            return None
        if candidate is None:
            candidate_obj = Candidate(
                candidate_id=f"candidate-{history.next_iteration}",
                description=f"iteration {history.next_iteration}",
                parameters={"iteration": history.next_iteration},
                parent_id=(history.trials[-1].candidate_id if history.trials else None),
            )
        elif isinstance(candidate, Candidate):
            candidate_obj = candidate
        else:
            candidate_obj = _candidate_from_dict(candidate)
        trial = TrialSpec(
            trial_id=f"{history.experiment_id}:trial:{history.next_iteration}",
            experiment_id=history.experiment_id,
            candidate=candidate_obj,
            iteration=history.next_iteration,
            input=dict(input or {}),
            constraints=history.spec.constraints,
        )
        history.proposed.append(trial)
        history.updated_at = self._clock()
        await self._save(history)
        return trial

    async def execute_trial(self, trial: TrialSpec) -> TrialResult:
        """Delegate execution to the injected port; never perform physical work here."""

        if self.execution_port is None:
            raise ExecutionPortUnavailable("ExperimentEngine requires an injected ExecutionPort")
        raw = await self.execution_port.execute(trial)
        result = self._normalize_result(trial, raw)
        # Resolve by the trial's explicit experiment id so a restarted engine
        # does not depend on a process-local ``_current_id``.
        history = await self._get(trial.experiment_id)
        if not any(item.trial_id == result.trial_id for item in history.trials):
            history.trials.append(result)
            history.next_iteration = max(history.next_iteration, trial.iteration + 1)
            if result.failed:
                history.failures += 1
            history.updated_at = self._clock()
            await self._save(history)
        return result

    async def evaluate(
        self,
        result: TrialResult,
        *,
        trial: TrialSpec | None = None,
        metric: MetricSpec | None = None,
    ) -> MetricResult:
        history = await self._get(trial.experiment_id if trial else None, result=result)
        metric_spec = metric or history.spec.metric
        if result.failed:
            return MetricResult(
                metric_spec.name,
                None,
                reproducible=False,
                details={"status": result.status, "error": result.error},
            )
        if self.metric_port is not None:
            raw = await self.metric_port.evaluate(trial, result, metric_spec)
            if isinstance(raw, MetricResult):
                return raw
            return MetricResult(
                name=str(raw.get("name", metric_spec.name)),
                value=float(raw["value"]) if raw.get("value") is not None else None,
                reproducible=bool(raw.get("reproducible", True)),
                details=dict(raw.get("details", {})),
            )
        value = result.metrics.get(metric_spec.name)
        return MetricResult(
            metric_spec.name,
            float(value) if value is not None else None,
            reproducible=metric_spec.reproducible,
            details={"source": "trial_result.metrics"},
        )

    async def compare(
        self,
        metric_result: MetricResult,
        *,
        experiment_id: str | None = None,
        baseline: Baseline | None = None,
        metric: MetricSpec | None = None,
    ) -> Comparison:
        history = await self._get(experiment_id)
        metric_spec = metric or history.spec.metric
        baseline_obj = baseline or history.spec.baseline
        base = baseline_obj.value(metric_result.name)
        candidate = metric_result.value
        delta = candidate - base if candidate is not None and base is not None else None
        improved = False
        if delta is not None and metric_result.reproducible:
            improved = delta > 0 if metric_spec.direction == "maximize" else delta < 0
        sufficient = improved and abs(delta or 0.0) >= metric_spec.minimum_improvement
        return Comparison(
            metric_name=metric_result.name,
            baseline_value=base,
            candidate_value=candidate,
            delta=delta,
            direction=metric_spec.direction,
            improved=improved,
            sufficient_improvement=sufficient,
            reproducible=metric_result.reproducible,
            details={"baseline_candidate_id": baseline_obj.candidate_id},
        )

    async def decide(
        self,
        comparison: Comparison,
        *,
        experiment_id: str | None = None,
        trial: TrialResult | None = None,
    ) -> ExperimentDecision:
        history = await self._get(experiment_id, result=trial)
        candidate_id = trial.candidate_id if trial else None
        if trial is not None and trial.failed:
            decision = "reject"
            reason = "failed trial is not eligible for acceptance recommendation"
        elif not comparison.reproducible:
            decision = "reject"
            reason = "metric is not reproducible"
        elif self._violates_constraints(history, comparison, trial):
            decision = "reject"
            reason = "candidate violates an experiment constraint"
        elif self._exceeds_budget(history, trial):
            decision = "reject"
            reason = "candidate exceeds an experiment budget"
        elif comparison.sufficient_improvement:
            decision = "accept"
            reason = "candidate meets the configured metric direction and improvement"
        else:
            decision = "reject"
            reason = "candidate does not meet the configured improvement threshold"
        item = ExperimentDecision(
            decision_id=_id(
                "decision",
                {
                    "experiment": history.experiment_id,
                    "candidate": candidate_id,
                    "comparison": comparison.to_dict(),
                },
            ),
            experiment_id=history.experiment_id,
            candidate_id=candidate_id,
            decision=decision,
            reason=reason,
            comparison=comparison,
            evidence_bundle=(
                {
                    "trial_id": trial.trial_id,
                    "evidence_refs": list(trial.evidence_refs),
                    "comparison": comparison.to_dict(),
                }
                if trial
                else {"comparison": comparison.to_dict()}
            ),
        )
        history.decisions.append(item)
        history.updated_at = self._clock()
        await self._save(history)
        return item

    async def next_iteration(self, *, experiment_id: str | None = None) -> TrialSpec | None:
        history = await self._get(experiment_id)
        if history.spec.early_stop and any(
            decision.decision == "accept" for decision in history.decisions
        ):
            return None
        return await self.propose_trial(experiment_id=history.experiment_id)

    async def summarize(self, *, experiment_id: str | None = None) -> ExperimentHistory:
        """Return persisted state; summarisation itself has no acceptance side effect."""

        return await self._get(experiment_id)

    async def _get(
        self,
        experiment_id: str | None = None,
        *,
        result: TrialResult | None = None,
    ) -> ExperimentHistory:
        requested = experiment_id or (result.outputs.get("experiment_id") if result else None)
        key = str(requested or self._current_id or "")
        if not key:
            raise ExperimentError("experiment_id is required before this operation")
        history = await self._load(key)
        if history is None:
            raise ExperimentError(f"experiment not found: {key}")
        return history

    async def _load(self, experiment_id: str) -> ExperimentHistory | None:
        if experiment_id in self._histories:
            return self._histories[experiment_id]
        history = await self.history_store.load(experiment_id)
        if history is not None:
            self._histories[experiment_id] = history
            self._current_id = experiment_id
        return history

    async def _save(self, history: ExperimentHistory) -> None:
        self._histories[history.experiment_id] = history
        self._current_id = history.experiment_id
        await self.history_store.save(history)

    def _budget_reason(self, history: ExperimentHistory) -> str | None:
        spec = history.spec
        if len(history.trials) >= spec.max_trials:
            return "max_trials"
        if (
            spec.max_failures is not None
            and history.failures > 0
            and history.failures >= spec.max_failures
        ):
            return "max_failures"
        if spec.max_cost is not None and history.cost_usd >= spec.max_cost:
            return "max_cost"
        if (
            spec.max_wall_time is not None
            and self._clock() - history.started_at >= spec.max_wall_time
        ):
            return "max_wall_time"
        return None

    @staticmethod
    def _violates_constraints(
        history: ExperimentHistory,
        comparison: Comparison,
        trial: TrialResult | None,
    ) -> bool:
        values: dict[str, float | None] = {
            comparison.metric_name: comparison.candidate_value,
            "cost_usd": trial.cost_usd if trial else None,
            "wall_time_s": trial.wall_time_s if trial else None,
        }
        return any(
            not constraint.check(values.get(constraint.name))
            for constraint in history.spec.constraints
        )

    @staticmethod
    def _exceeds_budget(history: ExperimentHistory, trial: TrialResult | None) -> bool:
        if trial is None:
            return False
        spec = history.spec
        if spec.max_cost is not None and history.cost_usd > spec.max_cost:
            return True
        if spec.max_wall_time is not None and trial.wall_time_s > spec.max_wall_time:
            return True
        return False

    @staticmethod
    def _normalize_result(trial: TrialSpec, raw: TrialResult | Mapping[str, Any]) -> TrialResult:
        result = (
            raw
            if isinstance(raw, TrialResult)
            else TrialResult(
                trial_id=str(raw.get("trial_id", trial.trial_id)),
                candidate_id=str(raw.get("candidate_id", trial.candidate.candidate_id)),
                status=str(raw.get("status", "completed")),
                metrics={str(k): float(v) for k, v in raw.get("metrics", {}).items()},
                outputs=dict(raw.get("outputs", {})),
                cost_usd=float(raw.get("cost_usd", 0.0)),
                wall_time_s=float(raw.get("wall_time_s", 0.0)),
                error=raw.get("error"),
                evidence_refs=tuple(str(item) for item in raw.get("evidence_refs", [])),
            )
        )
        if result.trial_id != trial.trial_id:
            raise ExperimentError(
                f"trial result id mismatch: expected {trial.trial_id}, got {result.trial_id}"
            )
        if result.candidate_id != trial.candidate.candidate_id:
            raise ExperimentError(
                "trial result candidate mismatch: "
                f"expected {trial.candidate.candidate_id}, got {result.candidate_id}"
            )
        return result


__all__ = [
    "Baseline",
    "Candidate",
    "Comparison",
    "Constraint",
    "ExecutionPort",
    "ExecutionPortUnavailable",
    "ExperimentDecision",
    "ExperimentEngine",
    "ExperimentError",
    "ExperimentHistory",
    "ExperimentHistoryStore",
    "ExperimentSpec",
    "Hypothesis",
    "JsonExperimentHistoryStore",
    "MemoryExperimentHistoryStore",
    "MetricPort",
    "MetricResult",
    "MetricSpec",
    "TrialResult",
    "TrialSpec",
]
