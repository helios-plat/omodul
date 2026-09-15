from __future__ import annotations

import pytest

from omodul.accepted_progress_projection import (
    AcceptanceVerdict,
    AcceptedProgressProjection,
    EvidenceRef,
    GoalRunEvent,
)
from omodul.code_knowledge_graph import CodeKnowledgeGraph
from omodul.experiment_engine import (
    Baseline,
    ExperimentEngine,
    ExperimentSpec,
    TrialResult,
)


@pytest.mark.asyncio
async def test_code_graph_calls_impact_revision_and_budget() -> None:
    graph = CodeKnowledgeGraph()
    first = await graph.index(
        "repo-a",
        "rev-1",
        paths={
            "src.py": "def root():\n    return helper()\n\ndef helper():\n    return 1\n",
            "test_src.py": "def test_helper():\n    return helper()\n",
        },
    )
    assert [node.ref.symbol for node in await graph.callees("root")] == ["helper"]
    assert any(ref.symbol == "test_helper" for ref in await graph.affected_tests("helper"))
    context = await graph.context_slice("root", token_budget=2)
    assert context.estimated_tokens <= context.token_budget

    second = await graph.index(
        "repo-a", "rev-2", paths={"renamed.py": "def root():\n    return 2\n"}
    )
    assert first.revision != second.revision
    assert await graph.resolve("root", repo_id="repo-a", revision="rev-1")
    assert not await graph.resolve("helper", repo_id="repo-a", revision="rev-2")
    with pytest.raises(KeyError, match="repo-b@rev-2"):
        await graph.resolve("root", repo_id="repo-b", revision="rev-2")

    delta = await graph.sync(
        "repo-a", "rev-3", paths={"renamed.py": "def other():\n    return 2\n"}
    )
    assert delta.removed_nodes and delta.added_nodes


@pytest.mark.asyncio
async def test_code_graph_partial_parse_is_diagnostic() -> None:
    graph = CodeKnowledgeGraph()
    snapshot = await graph.index("repo", "broken", paths={"broken.py": "def nope(:\n"})
    assert snapshot.diagnostics


@pytest.mark.asyncio
async def test_code_graph_resolves_local_module_dependencies() -> None:
    graph = CodeKnowledgeGraph()
    await graph.index(
        "repo",
        "rev",
        paths={
            "app.py": "import lib\ndef run():\n    return lib.helper()\n",
            "lib.py": "def helper():\n    return 1\n",
        },
    )
    dependencies = await graph.dependencies("app.py")
    assert [item.ref.path for item in dependencies] == ["lib.py"]
    assert any(item.ref.path == "app.py" for item in await graph.dependents("lib.py"))


@pytest.mark.asyncio
async def test_code_graph_detects_git_revision_staleness() -> None:
    class _Git:
        async def revision(self, *, repo_id: str) -> str:
            assert repo_id == "repo"
            return "new-revision"

    graph = CodeKnowledgeGraph(git=_Git())
    await graph.index("repo", "old-revision", paths={"app.py": "def run():\n    return 1\n"})
    assert await graph.stale(repo_id="repo", revision="old-revision")


@pytest.mark.asyncio
async def test_code_graph_json_store_restores_incremental_base(tmp_path) -> None:
    from omodul.code_knowledge_graph import JsonGraphStore

    store = JsonGraphStore(tmp_path / "graph")
    first = CodeKnowledgeGraph(graph_store=store)
    await first.index("repo", "rev-1", paths={"app.py": "def run():\n    return 1\n"})
    restarted = CodeKnowledgeGraph(graph_store=store)
    delta = await restarted.sync("repo", "rev-2", paths={"app.py": "def run():\n    return 2\n"})
    assert delta.from_revision == "rev-1"
    assert delta.changed_nodes


class _Execution:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, trial):
        self.calls += 1
        if self.calls == 1:
            return TrialResult(
                trial.trial_id,
                trial.candidate.candidate_id,
                status="failed",
                error="boom",
            )
        return TrialResult(
            trial.trial_id,
            trial.candidate.candidate_id,
            metrics={"score": 2.0},
            evidence_refs=("metric:e2",),
        )


@pytest.mark.asyncio
async def test_experiment_rejects_failed_trial_and_recommends_only_verified_improvement() -> None:
    engine = ExperimentEngine(execution_port=_Execution())
    await engine.create(
        ExperimentSpec(
            "experiment",
            "improve score",
            baseline=Baseline({"score": 1.0}),
            max_trials=3,
            metric_direction="maximize",
        )
    )
    trial = await engine.propose_trial()
    assert trial is not None
    failed = await engine.execute_trial(trial)
    metric = await engine.evaluate(failed, trial=trial)
    comparison = await engine.compare(metric, experiment_id="experiment")
    rejected = await engine.decide(comparison, experiment_id="experiment", trial=failed)
    assert rejected.decision == "reject"
    assert rejected.authority == "candidate_recommendation"

    next_trial = await engine.next_iteration(experiment_id="experiment")
    assert next_trial is not None
    successful = await engine.execute_trial(next_trial)
    comparison = await engine.compare(
        await engine.evaluate(successful, trial=next_trial), experiment_id="experiment"
    )
    recommendation = await engine.decide(comparison, experiment_id="experiment", trial=successful)
    assert recommendation.decision == "accept"
    assert recommendation.authority == "candidate_recommendation"

    projection = AcceptedProgressProjection()
    projection.apply_event(GoalRunEvent("candidate-done", next_trial.candidate.id, "completed"))
    assert not projection.accepted()
    projection.apply_verdict(
        AcceptanceVerdict(
            "verdict-1",
            next_trial.candidate.id,
            "PASS",
            evidence_refs=(EvidenceRef("metric:e2", kind="metric"),),
        )
    )
    assert projection.snapshot().accepted_ids == (next_trial.candidate.id,)


@pytest.mark.asyncio
async def test_experiment_history_persists_and_resumes_by_trial_id(tmp_path) -> None:
    from omodul.experiment_engine import JsonExperimentHistoryStore

    store = JsonExperimentHistoryStore(tmp_path)
    first = ExperimentEngine(history_store=store)
    await first.create(ExperimentSpec("persisted", "test"))
    trial = await first.propose_trial()
    assert trial is not None
    second = ExperimentEngine(history_store=store)
    restored = await second.create(ExperimentSpec("persisted", "test"))
    assert restored.experiment_id == "persisted"
    assert restored.proposed[0].trial_id == trial.trial_id


def test_progress_requires_external_acceptance_verdict_and_replays_deterministically() -> None:
    events = [
        GoalRunEvent("complete", "unit-a", "completed", attempt=1),
        GoalRunEvent("failed", "unit-b", "attempt_failed", attempt=1, payload={"reason": "x"}),
        GoalRunEvent("blocked", "unit-c", "blocked", payload={"reason": "dependency"}),
    ]
    projection = AcceptedProgressProjection()
    for event in events:
        projection.apply_event(event)
    assert not projection.accepted()
    projection.apply_verdict(AcceptanceVerdict("pass-2", "unit-a", "PASS", revision=2))
    projection.apply_verdict(AcceptanceVerdict("stale-fail", "unit-a", "FAIL", revision=1))
    expected = projection.snapshot()
    rebuilt = AcceptedProgressProjection().rebuild(
        events, [AcceptanceVerdict("pass-2", "unit-a", "PASS", revision=2)]
    )
    assert rebuilt == expected
    assert rebuilt.accepted_ids == ("unit-a",)
    assert rebuilt.blocked[0].unit_id == "unit-c"
