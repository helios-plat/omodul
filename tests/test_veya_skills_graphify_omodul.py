"""Tests for omodul.skills_guided_coding_workflow, graph_codebase_investigation_workflow, auto_etl_research_workflow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from omodul.skills_guided_coding_workflow import (
    Config as SkillsConfig,
    InputData as SkillsInput,
    skills_guided_coding_workflow,
)
from omodul.graph_codebase_investigation_workflow import (
    Config as GraphConfig,
    InputData as GraphInput,
    graph_codebase_investigation_workflow,
)
from omodul.auto_etl_research_workflow import (
    Config as EtlConfig,
    InputData as EtlInput,
    auto_etl_research_workflow,
)


# ============================================================================
# skills_guided_coding_workflow
# ============================================================================

class TestSkillsGuidedCodingWorkflow:
    def test_completed_with_no_violations(self, tmp_path: Path):
        def inject_sk(filename, *, registry, llm_caller):
            return {"matched_count": 1, "rules": ["Rule A"], "injected_prompt_prefix": "PREFIX"}

        def rule_check(code, *, domain):
            return {"domain": domain, "is_compliant": True, "violations": []}

        def llm(prompt, **kwargs):
            return "ok"

        registry = MagicMock()
        cfg = SkillsConfig(
            inject_sk=inject_sk,
            rule_check_op=rule_check,
            skills_registry=registry,
            llm_caller=llm,
        )
        inp = SkillsInput(file_path="contract.rs", code_content="fn safe() {}")
        result = skills_guided_coding_workflow(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_violations_found_status(self, tmp_path: Path):
        def inject_sk(filename, *, registry, llm_caller):
            return {"matched_count": 2, "rules": ["R1", "R2"], "injected_prompt_prefix": "P"}

        def rule_check(code, *, domain):
            return {
                "domain": domain, "is_compliant": False,
                "violations": [{"rule": "Issue!", "severity": "error"}],
            }

        def llm(prompt, **kwargs):
            return "ok"

        cfg = SkillsConfig(
            inject_sk=inject_sk,
            rule_check_op=rule_check,
            skills_registry=MagicMock(),
            llm_caller=llm,
        )
        inp = SkillsInput(file_path="buggy.rs", code_content="fn bad() { panic!(); }")
        result = skills_guided_coding_workflow(cfg, inp, tmp_path)
        assert result["status"] == "violations_found"

    def test_failed_on_exception(self, tmp_path: Path):
        def inject_sk(filename, *, registry, llm_caller):
            raise RuntimeError("injection failed")

        def llm(prompt, **kwargs):
            return "ok"

        # All dependencies present so inject_sk is actually called
        cfg = SkillsConfig(
            inject_sk=inject_sk, rule_check_op=None,
            skills_registry=MagicMock(), llm_caller=llm,
        )
        inp = SkillsInput(file_path="test.py")
        result = skills_guided_coding_workflow(cfg, inp, tmp_path)
        assert result["status"] == "failed"

    def test_no_injected_ops_works(self, tmp_path: Path):
        cfg = SkillsConfig(inject_sk=None, rule_check_op=None, skills_registry=None, llm_caller=None)
        inp = SkillsInput(file_path="test.py")
        result = skills_guided_coding_workflow(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_report_written(self, tmp_path: Path):
        def inject_sk(filename, *, registry, llm_caller):
            return {"matched_count": 1, "rules": ["PEP8"], "injected_prompt_prefix": "P"}

        def rule_check(code, *, domain):
            return {"domain": "solana", "is_compliant": True, "violations": []}

        def llm(prompt, **kwargs):
            return "ok"

        cfg = SkillsConfig(
            inject_sk=inject_sk, rule_check_op=rule_check,
            skills_registry=MagicMock(), llm_caller=llm,
        )
        inp = SkillsInput(file_path="lib.rs", code_content="fn f() {}")
        result = skills_guided_coding_workflow(cfg, inp, tmp_path)
        assert Path(result["report_path"]).exists()

    def test_config_has_enabled_pillars(self):
        assert SkillsConfig._enabled_pillars == {"fingerprint", "decision_trail", "report", "cost"}

    def test_domain_inferred(self, tmp_path: Path):
        def inject_sk(filename, *, registry, llm_caller):
            return {"matched_count": 0, "rules": [], "injected_prompt_prefix": ""}

        def rule_check(code, *, domain):
            return {"domain": domain, "is_compliant": True, "violations": []}

        def llm(prompt, **kwargs):
            return "ok"

        registry = MagicMock()
        cfg = SkillsConfig(
            inject_sk=inject_sk, rule_check_op=rule_check,
            skills_registry=registry, llm_caller=llm,
        )
        inp = SkillsInput(file_path="solana_program.rs", code_content="fn x() {}", domain="solana")
        result = skills_guided_coding_workflow(cfg, inp, tmp_path)
        assert result["status"] == "completed"


# ============================================================================
# graph_codebase_investigation_workflow
# ============================================================================

class TestGraphCodebaseInvestigationWorkflow:
    def test_completed_workflow(self, tmp_path: Path):
        def parse_op(code, *, file_path):
            return {
                "status": "success",
                "nodes": [
                    {"id": "test.py::Service", "type": "class", "name": "Service"},
                    {"id": "test.py::process", "type": "function", "name": "process"},
                ],
                "edges": [
                    {"source": "test.py::Service", "target": "test.py::process", "relation": "contains"},
                ],
            }

        def impact_sk(graph, *, target_node_id, llm_caller):
            return {
                "impacted_nodes": ["test.py::Service"],
                "downstream_deps": [],
                "risk_level": "medium",
                "plan": "Test Service first.",
            }

        def llm(prompt, **kwargs):
            return "ok"

        cfg = GraphConfig(parse_op=parse_op, impact_sk=impact_sk, llm_caller=llm)
        inp = GraphInput(
            file_content="class Service:\n    def process(self): pass",
            file_path="test.py",
            target_node="test.py::process",
        )
        result = graph_codebase_investigation_workflow(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_no_ops_works(self, tmp_path: Path):
        cfg = GraphConfig(parse_op=None, impact_sk=None, llm_caller=None)
        inp = GraphInput()
        result = graph_codebase_investigation_workflow(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_failed_on_exception(self, tmp_path: Path):
        def bad_parse(code, **kwargs):
            raise RuntimeError("parser crash")

        cfg = GraphConfig(parse_op=bad_parse, impact_sk=None, llm_caller=None)
        inp = GraphInput(file_content="x")
        result = graph_codebase_investigation_workflow(cfg, inp, tmp_path)
        assert result["status"] == "failed"

    def test_report_written(self, tmp_path: Path):
        def parse_op(code, *, file_path):
            return {"status": "success", "nodes": [], "edges": []}

        def impact_sk(graph, *, target_node_id, llm_caller):
            return {"impacted_nodes": [], "downstream_deps": [], "risk_level": "low", "plan": "OK"}

        def llm(prompt, **kwargs):
            return "{}"

        cfg = GraphConfig(parse_op=parse_op, impact_sk=impact_sk, llm_caller=llm)
        inp = GraphInput(file_content="pass", file_path="empty.py", target_node="empty.py::none")
        result = graph_codebase_investigation_workflow(cfg, inp, tmp_path)
        assert Path(result["report_path"]).exists()

    def test_config_has_enabled_pillars(self):
        assert GraphConfig._enabled_pillars == {"fingerprint", "decision_trail", "report"}


# ============================================================================
# auto_etl_research_workflow
# ============================================================================

class TestAutoEtlResearchWorkflow:
    def test_completed_workflow(self, tmp_path: Path):
        def scraping_sk(url, *, scraper_op, extractor_op, llm_caller):
            return {
                "url": url, "anchor_confidence": 0.95,
                "summary": "Great research content.", "status": "success",
            }

        def curation_sk(data, *, normalize_op, pipeline_store):
            return {"status": "curated", "processed_records": 1, "inferred_schema_sample": {"a": "b"}}

        def llm(prompt, **kwargs):
            return "summary"

        cfg = EtlConfig(
            scraping_sk=scraping_sk, curation_sk=curation_sk,
            scraper_op=MagicMock(), extractor_op=MagicMock(),
            normalize_op=MagicMock(), pipeline_store=MagicMock(),
            llm_caller=llm,
        )
        inp = EtlInput(url="https://example.com/article")
        result = auto_etl_research_workflow(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_no_ops_works(self, tmp_path: Path):
        cfg = EtlConfig(
            scraping_sk=None, curation_sk=None,
            scraper_op=None, extractor_op=None,
            normalize_op=None, pipeline_store=None, llm_caller=None,
        )
        inp = EtlInput(url="https://test.com")
        result = auto_etl_research_workflow(cfg, inp, tmp_path)
        assert result["status"] == "completed"

    def test_failed_on_exception(self, tmp_path: Path):
        def bad_scraping(url, **kwargs):
            raise ConnectionError("DNS failure")

        cfg = EtlConfig(scraping_sk=bad_scraping, llm_caller=MagicMock())
        inp = EtlInput(url="https://fail.com")
        result = auto_etl_research_workflow(cfg, inp, tmp_path)
        assert result["status"] == "failed"

    def test_report_written(self, tmp_path: Path):
        def scraping_sk(url, *, scraper_op, extractor_op, llm_caller):
            return {"url": url, "summary": "Test", "anchor_confidence": 0.8, "status": "success"}

        def curation_sk(data, *, normalize_op, pipeline_store):
            return {"status": "curated", "processed_records": 1, "inferred_schema_sample": {}}

        def llm(prompt, **kwargs):
            return "ok"

        cfg = EtlConfig(
            scraping_sk=scraping_sk, curation_sk=curation_sk,
            scraper_op=MagicMock(), extractor_op=MagicMock(),
            normalize_op=MagicMock(), pipeline_store=MagicMock(),
            llm_caller=llm,
        )
        inp = EtlInput(url="https://report.com")
        result = auto_etl_research_workflow(cfg, inp, tmp_path)
        assert Path(result["report_path"]).exists()

    def test_config_has_enabled_pillars(self):
        assert EtlConfig._enabled_pillars == {"fingerprint", "decision_trail", "report", "cost"}
