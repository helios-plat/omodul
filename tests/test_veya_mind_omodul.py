"""Tests for Veya 终极前沿认知能力 — omodul 层 (1 个).

Covers: self_synthesis_skill_workflow（技能自主编写 + 热加载事务）.
"""

from __future__ import annotations

import pathlib


class _CodeLLM:
    def __call__(self, prompt, *, temperature=0.0):
        return "def magic():\n    return 42"


def _hotloader(code, *, module_name="auto_skill"):
    from oprim import dynamic_code_hotload

    return dynamic_code_hotload(code, module_name=module_name)


class TestSelfSynthesisSkillWorkflow:
    async def test_synthesize_and_hotload(self, tmp_path: pathlib.Path):
        from omodul import (
            SelfSynthesisSkillConfig,
            SelfSynthesisSkillInput,
            self_synthesis_skill_workflow,
        )

        r = await self_synthesis_skill_workflow(
            SelfSynthesisSkillConfig(),
            SelfSynthesisSkillInput(
                requirement="magic 函数",
                module_name="auto_skill_test_1",
                llm_caller=_CodeLLM(),
                hotloader=_hotloader,
            ),
            tmp_path,
        )
        assert r["status"] == "completed", r
        assert "magic" in r["findings"]["exported_functions"]
        assert "auto_skill_test_1" in r["summary"]
        assert r["report_path"] and pathlib.Path(r["report_path"]).exists()
        assert r["decision_trail"]["steps"] >= 2
        import sys

        assert sys.modules["auto_skill_test_1"].magic() == 42

    async def test_missing_injections_fails(self, tmp_path: pathlib.Path):
        from omodul import (
            SelfSynthesisSkillConfig,
            SelfSynthesisSkillInput,
            self_synthesis_skill_workflow,
        )

        r = await self_synthesis_skill_workflow(
            SelfSynthesisSkillConfig(),
            SelfSynthesisSkillInput(requirement="x"),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "injected" in r["error"]["message"]

    async def test_hotload_failure_fails(self, tmp_path: pathlib.Path):
        from omodul import (
            SelfSynthesisSkillConfig,
            SelfSynthesisSkillInput,
            self_synthesis_skill_workflow,
        )

        def bad_hotloader(code, *, module_name="auto_skill"):
            return {"status": "failed", "error": "boom"}

        r = await self_synthesis_skill_workflow(
            SelfSynthesisSkillConfig(),
            SelfSynthesisSkillInput(
                requirement="x", llm_caller=_CodeLLM(), hotloader=bad_hotloader
            ),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "boom" in r["error"]["message"]

    async def test_llm_error_captured(self, tmp_path: pathlib.Path):
        from omodul import (
            SelfSynthesisSkillConfig,
            SelfSynthesisSkillInput,
            self_synthesis_skill_workflow,
        )

        class BrokenLLM:
            def __call__(self, prompt, *, temperature=0.0):
                raise RuntimeError("llm down")

        r = await self_synthesis_skill_workflow(
            SelfSynthesisSkillConfig(),
            SelfSynthesisSkillInput(
                requirement="x", llm_caller=BrokenLLM(), hotloader=_hotloader
            ),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "llm down" in r["error"]["message"]
