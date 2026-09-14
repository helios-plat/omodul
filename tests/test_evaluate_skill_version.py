import pytest

from omodul.evaluate_skill_version import SkillEvaluationConfig, evaluate_skill_version


@pytest.mark.asyncio
async def test_evaluate_skill_version_qualified(tmp_path):
    result = await evaluate_skill_version(
        SkillEvaluationConfig(),
        {
            "dataset": ["case"],
            "baseline": {"metrics": {"score": 1.0}},
            "candidate": {"metrics": {"score": 1.1}},
            "dimensions": {"score": {"direction": "higher_is_better"}},
            "baseline_executor": lambda dataset: {"metrics": {"score": 1.0}},
            "candidate_executor": lambda dataset: {"metrics": {"score": 1.1}},
        },
        tmp_path,
    )
    assert result["status"] == "completed"
    assert result["qualified"] is True
    assert result["evidence"]["baseline_candidate_isolated"] is True


@pytest.mark.asyncio
async def test_evaluate_skill_version_missing_metric_fails_closed(tmp_path):
    result = await evaluate_skill_version(
        {},
        {
            "dataset": ["case"],
            "baseline_executor": lambda dataset: {"metrics": {"score": 1.0}},
            "candidate_executor": lambda dataset: {"metrics": {}},
            "dimensions": ["score"],
        },
        tmp_path,
    )
    assert result["status"] == "failed"
    assert result["error"]["type"] == "evaluation_failed"


@pytest.mark.asyncio
async def test_evaluate_skill_version_execution_failure(tmp_path):
    def broken(dataset):
        raise RuntimeError("boom")

    result = await evaluate_skill_version(
        {},
        {"dataset": ["case"], "baseline_executor": broken, "candidate_executor": lambda _: {}},
        tmp_path,
    )
    assert result["error"]["type"] == "execution_failed"


@pytest.mark.asyncio
async def test_evaluate_skill_version_assertion_failure_and_missing_cost(tmp_path):
    result = await evaluate_skill_version(
        {},
        {
            "dataset": ["case"],
            "baseline_executor": lambda _: {"metrics": {"score": 1.0}},
            "candidate_executor": lambda _: {"metrics": {"score": 1.0}},
            "dimensions": ["score"],
            "assertions": lambda *_: False,
        },
        tmp_path,
    )
    assert result["error"]["type"] == "evaluation_failed"


@pytest.mark.asyncio
async def test_evaluate_skill_version_preserves_partial_execution_evidence(tmp_path):
    async def candidate(dataset):
        return {"metrics": {"score": 1.0}, "partial": True}

    result = await evaluate_skill_version(
        {},
        {
            "dataset": ["case"],
            "baseline_executor": lambda _: {"metrics": {"score": 1.0}},
            "candidate_executor": candidate,
            "dimensions": ["score"],
        },
        tmp_path,
    )
    assert result["status"] == "completed"
    assert result["candidate"]["partial"] is True
    assert result["evidence"]["cost_data_present"] is False


@pytest.mark.asyncio
async def test_evaluate_skill_version_empty_dataset(tmp_path):
    result = await evaluate_skill_version({}, {"dataset": []}, tmp_path)
    assert result["error"]["type"] == "evaluation_failed"
