import pytest

from omodul.qualify_change import qualify_change


@pytest.mark.asyncio
async def test_qualify_change_success_and_ratchet_failure(tmp_path):
    data = {
        "contract": {"required": ["status"], "expect": {"status": "ok"}},
        "observed": {"status": "ok"},
        "evidence": [{"type": "test", "name": "unit"}],
        "claim": {"id": "bug-1"},
        "pre_change_evidence": {"claim_id": "bug-1", "status": "failed"},
        "failure_contract": {"id": "bug-1"},
        "baseline": {"metrics": {"x": 1}},
        "candidate": {"metrics": {"x": 1}},
        "rules": {"directions": {"x": "higher_is_better"}},
        "tests": {"passed": ["unit"]},
    }
    result = await qualify_change({}, data, tmp_path)
    assert result["status"] == "completed" and result["qualified"] is True
    data["candidate"] = {"metrics": {"x": 0.1}}
    result = await qualify_change({}, data, tmp_path)
    assert result["qualified"] is False and "ratchet" in result["blockers"]


@pytest.mark.asyncio
async def test_qualify_change_insufficient_evidence(tmp_path):
    result = await qualify_change(
        {}, {"contract": {"required": ["x"]}, "observed": {}, "evidence": []}, tmp_path
    )
    assert result["status"] == "completed"
    assert result["qualified"] is False
    assert "proven_red" in result["blockers"]
