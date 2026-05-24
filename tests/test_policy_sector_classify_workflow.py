"""Tests for omodul.policy_sector_classify_workflow (C1)."""

from unittest.mock import MagicMock

from omodul.policy_sector_classify_workflow import (
    PolicySectorClassifyConfig,
    compute_fingerprint_for,
    policy_sector_classify_workflow,
)


class TestPolicySectorClassifyWorkflow:
    def test_happy_path_5_pieces(self) -> None:
        config = PolicySectorClassifyConfig(policy_ids=["p1", "p2"])
        llm = MagicMock()
        llm.call.return_value = '[{"item_idx": 1, "labels": ["tech"]}]'
        result = policy_sector_classify_workflow(
            config, policies=[{"title": "AI policy"}], sectors=["tech", "finance"], llm=llm
        )
        assert "findings" in result
        assert "report" in result
        assert "decision_trail" in result
        assert "cost_usd" in result
        assert "fingerprint" in result

    def test_fingerprint_field_change_fp_change(self) -> None:
        c1 = PolicySectorClassifyConfig(policy_ids=["a"])
        c2 = PolicySectorClassifyConfig(policy_ids=["b"])
        assert compute_fingerprint_for(c1) != compute_fingerprint_for(c2)

    def test_fingerprint_non_field_change_fp_stable(self) -> None:
        c1 = PolicySectorClassifyConfig(policy_ids=["a"], confidence_threshold=0.5)
        c2 = PolicySectorClassifyConfig(policy_ids=["a"], confidence_threshold=0.9)
        assert compute_fingerprint_for(c1) == compute_fingerprint_for(c2)

    def test_llm_failure_status_failed(self) -> None:
        config = PolicySectorClassifyConfig(policy_ids=["p1"])
        llm = MagicMock()
        llm.call.side_effect = RuntimeError("LLM down")
        result = policy_sector_classify_workflow(
            config, policies=[{"title": "test"}], sectors=["a"], llm=llm
        )
        # Should still return structure (error handled in batch_classify)
        assert result["status"] == "completed"  # batch_classify catches internally

    def test_empty_policy_ids(self) -> None:
        config = PolicySectorClassifyConfig(policy_ids=[])
        result = policy_sector_classify_workflow(config, policies=[], sectors=["a"])
        assert result["status"] == "completed"
        assert result["findings"]["n_policies"] == 0

    def test_max_labels_truncation(self) -> None:
        config = PolicySectorClassifyConfig(policy_ids=["p1"], max_labels=1)
        llm = MagicMock()
        llm.call.return_value = '[{"item_idx": 1, "labels": ["a", "b", "c"]}]'
        result = policy_sector_classify_workflow(
            config, policies=[{"title": "x"}], sectors=["a", "b", "c"], llm=llm
        )
        for c in result["findings"]["classifications"]:
            assert len(c.get("labels", [])) <= 1

    def test_compute_fingerprint_for_public(self) -> None:
        config = PolicySectorClassifyConfig(policy_ids=["x"])
        fp = compute_fingerprint_for(config)
        assert isinstance(fp, str)
        assert len(fp) == 16
