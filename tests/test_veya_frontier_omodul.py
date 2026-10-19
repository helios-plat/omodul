"""Tests for Veya 前沿探索元素 — omodul 层 (1 个).

Covers: root_cause_analysis_workflow.
"""

from __future__ import annotations

import pathlib


def _causal_builder(trail, *, strict_mode=False):
    return {
        "node_count": 2,
        "edge_count": 1,
        "graph": {
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "edges": [{"source": "n1", "target": "n2"}],
        },
    }


def _counterfactual_skill(causal_graph, failed_step_id, *, llm_caller, alternative_action=""):
    return {
        "status": "success",
        "would_succeed": True,
        "root_cause": "缺判空",
        "explanation": "补判空即可",
    }


class TestRootCauseAnalysisWorkflow:
    async def test_completed(self, tmp_path: pathlib.Path):
        from omodul import (
            RootCauseAnalysisConfig,
            RootCauseAnalysisInput,
            root_cause_analysis_workflow,
        )

        r = await root_cause_analysis_workflow(
            RootCauseAnalysisConfig(),
            RootCauseAnalysisInput(
                decision_trail=[{"type": "action", "action": "read"}],
                failed_step="step_0_action",
                causal_builder=_causal_builder,
                counterfactual_skill=_counterfactual_skill,
            ),
            tmp_path,
        )
        assert r["status"] == "completed", r
        assert r["findings"]["root_cause"] == "缺判空"
        assert r["findings"]["would_succeed"] is True
        assert r["findings"]["graph_stats"] == {"nodes": 2, "edges": 1}
        assert r["report_path"] and pathlib.Path(r["report_path"]).exists()
        assert r["decision_trail"]["steps"] >= 2
        assert "缺判空" in r["summary"]

    async def test_empty_trail_fails(self, tmp_path: pathlib.Path):
        from omodul import (
            RootCauseAnalysisConfig,
            RootCauseAnalysisInput,
            root_cause_analysis_workflow,
        )

        r = await root_cause_analysis_workflow(
            RootCauseAnalysisConfig(),
            RootCauseAnalysisInput(
                decision_trail=[], causal_builder=_causal_builder,
                counterfactual_skill=_counterfactual_skill,
            ),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "decision trail" in r["error"]["message"]

    async def test_missing_injections_fails(self, tmp_path: pathlib.Path):
        from omodul import (
            RootCauseAnalysisConfig,
            RootCauseAnalysisInput,
            root_cause_analysis_workflow,
        )

        r = await root_cause_analysis_workflow(
            RootCauseAnalysisConfig(),
            RootCauseAnalysisInput(
                decision_trail=[{"type": "action"}],
                causal_builder=None,
                counterfactual_skill=_counterfactual_skill,
            ),
            tmp_path,
        )
        assert r["status"] == "failed" and "injected" in r["error"]["message"]

    async def test_skill_failure_captured(self, tmp_path: pathlib.Path):
        from omodul import (
            RootCauseAnalysisConfig,
            RootCauseAnalysisInput,
            root_cause_analysis_workflow,
        )

        def exploding_skill(causal_graph, failed_step_id, *, llm_caller, alternative_action=""):
            raise RuntimeError("cf skill boom")

        r = await root_cause_analysis_workflow(
            RootCauseAnalysisConfig(),
            RootCauseAnalysisInput(
                decision_trail=[{"type": "action"}],
                failed_step="s0",
                causal_builder=_causal_builder,
                counterfactual_skill=exploding_skill,
            ),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "boom" in r["error"]["message"]
