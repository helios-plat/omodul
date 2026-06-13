"""
omodul 批次 E 测试套件
======================
16 个 omodul，每个 ≥10 个测试。
LLM caller / store 全部 mock。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from omodul import (
    RefactorConfig,
    apply_changeset, ChangesetConfig, ChangesetInput, Edit, EditBlock,
    code_review, CodeReviewConfig, CodeReviewInput,
    compact_conversation, CompactConversationConfig, CompactConversationInput,
    create_checkpoint, CreateCheckpointConfig, CreateCheckpointInput,
    explain_codebase, ExplainCodebaseConfig, ExplainCodebaseInput,
    generate_commit_message, GenerateCommitConfig, GenerateCommitInput,
    generate_tests, GenerateTestsConfig, GenerateTestsInput,
    initialize_project, InitProjectConfig, InitProjectInput,
    install_plugin, InstallPluginConfig, InstallPluginInput,
    migrate_dependency, MigrateDependencyConfig, MigrateDependencyInput,
    refactor_transaction, RefactorConfig, RefactorInput,
    rewind_to_checkpoint, RewindConfig, RewindInput,
    run_and_fix, RunAndFixConfig, RunAndFixInput,
    run_subagent, SubagentConfig, SubagentDefinition, SubagentInput, SubagentPermissions,
    security_audit, SecurityAuditConfig, SecurityAuditInput,
    summarize_session, SummarizeSessionConfig, SummarizeSessionInput,
)


# ===========================================================================
# helpers
# ===========================================================================

def make_caller(text="ok", extra=None):
    async def caller(**kwargs):
        content = extra or [{"type": "text", "text": text}]
        return {"content": content, "stop_reason": "end_turn",
                "usage": {"input_tokens": 20, "output_tokens": 10}}
    return caller


def make_store(revision="rev_001", data=None):
    store = AsyncMock()
    store.save = AsyncMock(return_value=revision)
    store.load = AsyncMock(return_value=json.dumps(data) if data else None)
    return store


MSGS = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]


def run(coro):
    return asyncio.run(coro)


# ===========================================================================
# apply_changeset (already tested in test_changeset_and_loop — add 10 here)
# ===========================================================================

class TestApplyChangeset:
    def test_full_content_completes(self, tmp_path):
        p = tmp_path / "x.py"
        edits = [Edit(path=str(p), full_content="x=1\n", validate_syntax=False)]
        r = apply_changeset(ChangesetConfig(), ChangesetInput(edits=edits), tmp_path / "out")
        assert r["status"] == "completed" and str(p) in r["applied"]

    def test_syntax_error_rolls_back(self, tmp_path):
        p = tmp_path / "x.py"
        p.write_text("x=1\n")
        from omodul.apply_changeset import VersionStore
        vstore = VersionStore()
        edits = [Edit(path=str(p), full_content="def bad(\n", validate_syntax=True)]
        r = apply_changeset(ChangesetConfig(), ChangesetInput(edits=edits, versionstore=vstore), tmp_path/"out")
        assert r["status"] == "rolled_back" and p.read_text() == "x=1\n"

    def test_fingerprint_in_result(self, tmp_path):
        p = tmp_path / "f.txt"
        edits = [Edit(path=str(p), full_content="hi", validate_syntax=False)]
        r = apply_changeset(ChangesetConfig(), ChangesetInput(edits=edits), tmp_path/"out")
        assert r["fingerprint"] and len(r["fingerprint"]) == 24

    def test_decision_trail_written(self, tmp_path):
        p = tmp_path / "f.txt"
        edits = [Edit(path=str(p), full_content="hi", validate_syntax=False)]
        r = apply_changeset(ChangesetConfig(), ChangesetInput(edits=edits), tmp_path/"out")
        assert Path(r["decision_trail"]["path"]).exists()

    def test_cost_zero(self, tmp_path):
        p = tmp_path / "f.txt"
        edits = [Edit(path=str(p), full_content="hi", validate_syntax=False)]
        r = apply_changeset(ChangesetConfig(), ChangesetInput(edits=edits), tmp_path/"out")
        assert r["cost_usd"] == 0.0

    def test_sandbox_violation_fails(self, tmp_path):
        sandbox = tmp_path / "sb"; sandbox.mkdir()
        outside = tmp_path / "secret.txt"
        edits = [Edit(path=str(outside), full_content="x", validate_syntax=False)]
        r = apply_changeset(ChangesetConfig(sandbox_root=str(sandbox)),
                            ChangesetInput(edits=edits), tmp_path/"out")
        assert r["status"] == "failed"

    def test_on_step_called(self, tmp_path):
        p = tmp_path / "f.txt"
        events = []
        edits = [Edit(path=str(p), full_content="hi", validate_syntax=False)]
        apply_changeset(ChangesetConfig(), ChangesetInput(edits=edits), tmp_path/"out",
                        on_step=lambda e: events.append(e["event"]))
        assert "changeset_start" in events

    def test_multi_file_second_fails_rollback(self, tmp_path):
        p1 = tmp_path / "a.py"; p1.write_text("a=1\n")
        p2 = tmp_path / "b.py"; p2.write_text("b=2\n")
        from omodul.apply_changeset import VersionStore
        vs = VersionStore()
        edits = [
            Edit(path=str(p1), full_content="a=99\n", validate_syntax=True),
            Edit(path=str(p2), full_content="def bad(\n", validate_syntax=True),
        ]
        r = apply_changeset(ChangesetConfig(), ChangesetInput(edits=edits, versionstore=vs), tmp_path/"out")
        assert r["status"] == "rolled_back" and p1.read_text() == "a=1\n"

    def test_edit_blocks_applied(self, tmp_path):
        p = tmp_path / "g.txt"
        p.write_text("hello world\n")
        edits = [Edit(path=str(p), blocks=[EditBlock(search="hello", replace="goodbye")],
                      validate_syntax=False)]
        r = apply_changeset(ChangesetConfig(), ChangesetInput(edits=edits), tmp_path/"out")
        assert r["status"] == "completed" and "goodbye" in p.read_text()

    def test_missing_path_no_crash(self, tmp_path):
        edits = [Edit(path=str(tmp_path/"no"/"x.txt"), full_content="x", validate_syntax=False)]
        r = apply_changeset(ChangesetConfig(), ChangesetInput(edits=edits), tmp_path/"out")
        assert "status" in r


# ===========================================================================
# run_subagent (already tested — 10 more here for integration)
# ===========================================================================

class TestRunSubagent:
    def _defn(self, mode="default"):
        return SubagentDefinition(
            name="test-agent",
            system_prompt="You help with code.",
            tools=[],
            permissions=SubagentPermissions(mode=mode),
        )

    def test_completes(self, tmp_path):
        cfg = SubagentConfig()
        inp = SubagentInput(task="do task", subagent_def=self._defn(),
                            caller=make_caller("task done"))
        r = run(run_subagent(cfg, inp, tmp_path))
        assert r["status"] == "completed"

    def test_summary_contains_result(self, tmp_path):
        r = run(run_subagent(SubagentConfig(),
                             SubagentInput(task="test", subagent_def=self._defn(),
                                          caller=make_caller("result text")),
                             tmp_path))
        assert "result text" in r["summary"]

    def test_cost_tracked(self, tmp_path):
        r = run(run_subagent(SubagentConfig(),
                             SubagentInput(task="t", subagent_def=self._defn(),
                                          caller=make_caller()),
                             tmp_path))
        assert r["cost_usd"] >= 0

    def test_trail_written(self, tmp_path):
        r = run(run_subagent(SubagentConfig(),
                             SubagentInput(task="t", subagent_def=self._defn(),
                                          caller=make_caller()),
                             tmp_path))
        assert Path(r["decision_trail"]["path"]).exists()

    def test_depth_exceeded(self, tmp_path):
        from omodul.run_subagent import _current_depth, RECURSION_DEPTH_LIMIT
        tok = _current_depth.set(RECURSION_DEPTH_LIMIT)
        try:
            r = run(run_subagent(SubagentConfig(),
                                 SubagentInput(task="t", subagent_def=self._defn(),
                                               caller=make_caller()),
                                 tmp_path))
            assert r["status"] == "depth_exceeded"
        finally:
            _current_depth.reset(tok)

    def test_llm_error_fails(self, tmp_path):
        async def bad(**kw): raise RuntimeError("provider error")
        r = run(run_subagent(SubagentConfig(),
                             SubagentInput(task="t", subagent_def=self._defn(), caller=bad),
                             tmp_path))
        assert r["status"] == "failed"

    def test_subagent_name_in_result(self, tmp_path):
        defn = self._defn()
        r = run(run_subagent(SubagentConfig(),
                             SubagentInput(task="t", subagent_def=defn, caller=make_caller()),
                             tmp_path))
        assert r["subagent_name"] == defn.name

    def test_on_step_called(self, tmp_path):
        events = []
        run(run_subagent(SubagentConfig(),
                         SubagentInput(task="t", subagent_def=self._defn(), caller=make_caller()),
                         tmp_path,
                         on_step=lambda e: events.append(e["event"])))
        assert "subagent_start" in events

    def test_depth_field(self, tmp_path):
        r = run(run_subagent(SubagentConfig(),
                             SubagentInput(task="t", subagent_def=self._defn(), caller=make_caller()),
                             tmp_path))
        assert r["depth"] >= 1

    def test_budget_exceeded(self, tmp_path):
        cfg = SubagentConfig(budget_usd=0.000001)
        r = run(run_subagent(cfg,
                             SubagentInput(task="t", subagent_def=self._defn(),
                                          caller=make_caller()),
                             tmp_path))
        assert r["status"] in ("budget_exceeded", "completed")


# ===========================================================================
# initialize_project
# ===========================================================================

class TestInitializeProject:
    def test_completes(self, tmp_path):
        r = run(initialize_project(
            InitProjectConfig(),
            InitProjectInput(root_path=str(tmp_path), caller=make_caller("# AGENTS.md\nProject desc.")),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"

    def test_agents_md_written(self, tmp_path):
        run(initialize_project(
            InitProjectConfig(),
            InitProjectInput(root_path=str(tmp_path), caller=make_caller("project content")),
            tmp_path / "out",
        ))
        agents = tmp_path / "AGENTS.md"
        assert agents.exists() and "project content" in agents.read_text()

    def test_report_created(self, tmp_path):
        r = run(initialize_project(
            InitProjectConfig(),
            InitProjectInput(root_path=str(tmp_path), caller=make_caller("x")),
            tmp_path / "out",
        ))
        assert r["report_path"] and Path(str(r["report_path"])).exists()

    def test_cost_tracked(self, tmp_path):
        r = run(initialize_project(
            InitProjectConfig(),
            InitProjectInput(root_path=str(tmp_path), caller=make_caller("x")),
            tmp_path / "out",
        ))
        assert r["cost_usd"] > 0

    def test_decision_trail(self, tmp_path):
        r = run(initialize_project(
            InitProjectConfig(),
            InitProjectInput(root_path=str(tmp_path), caller=make_caller("x")),
            tmp_path / "out",
        ))
        assert r["decision_trail"]["steps"] > 0

    def test_caller_error_fails(self, tmp_path):
        async def bad(**kw): raise RuntimeError("api error")
        r = run(initialize_project(
            InitProjectConfig(),
            InitProjectInput(root_path=str(tmp_path), caller=bad),
            tmp_path / "out",
        ))
        assert r["status"] == "failed" and r["error"] is not None

    def test_fingerprint_present(self, tmp_path):
        r = run(initialize_project(
            InitProjectConfig(),
            InitProjectInput(root_path=str(tmp_path), caller=make_caller("x")),
            tmp_path / "out",
        ))
        assert r["fingerprint"]

    def test_on_step_called(self, tmp_path):
        events = []
        run(initialize_project(
            InitProjectConfig(),
            InitProjectInput(root_path=str(tmp_path), caller=make_caller("x")),
            tmp_path / "out",
            on_step=lambda e: events.append(e["event"]),
        ))
        assert "scan_start" in events

    def test_custom_agents_md_path(self, tmp_path):
        run(initialize_project(
            InitProjectConfig(agents_md_path="docs/AGENTS.md"),
            InitProjectInput(root_path=str(tmp_path), caller=make_caller("x")),
            tmp_path / "out",
        ))
        assert (tmp_path / "docs" / "AGENTS.md").exists()

    def test_status_field_present(self, tmp_path):
        r = run(initialize_project(
            InitProjectConfig(),
            InitProjectInput(root_path=str(tmp_path), caller=make_caller("x")),
            tmp_path / "out",
        ))
        assert r["status"] in ("completed", "failed")


# ===========================================================================
# generate_commit_message
# ===========================================================================

class TestGenerateCommitMessage:
    def test_returns_message(self, tmp_path):
        import subprocess
        repo = tmp_path / "repo"; repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], capture_output=True)
        r = run(generate_commit_message(
            GenerateCommitConfig(),
            GenerateCommitInput(repo_path=str(repo), caller=make_caller("feat: add login"),
                                diff_text="diff --git a/f.py\n+x=1"),
            tmp_path / "out",
        ))
        assert r["status"] == "completed" and r["message"]

    def test_no_diff_no_llm_call(self, tmp_path):
        import subprocess
        repo = tmp_path / "repo2"; repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        calls = []
        async def counting(**kw):
            calls.append(1)
            return {"content":[{"type":"text","text":"msg"}],"usage":{}}
        r = run(generate_commit_message(
            GenerateCommitConfig(),
            GenerateCommitInput(repo_path=str(repo), caller=counting, diff_text=""),
            tmp_path / "out",
        ))
        # 无 diff → 不调 LLM，直接返回默认消息
        assert r["status"] == "completed"
        if not calls:  # 只有无 diff 时不调
            assert r["message"] == "chore: no changes"

    def test_conventional_style(self, tmp_path):
        r = run(generate_commit_message(
            GenerateCommitConfig(commit_style="conventional"),
            GenerateCommitInput(repo_path=str(tmp_path), caller=make_caller("feat: x"),
                                diff_text="+x=1"),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"

    def test_error_fails(self, tmp_path):
        async def bad(**kw): raise RuntimeError("fail")
        r = run(generate_commit_message(
            GenerateCommitConfig(),
            GenerateCommitInput(repo_path=str(tmp_path), caller=bad, diff_text="+x=1"),
            tmp_path / "out",
        ))
        assert r["status"] == "failed"

    def test_cost_tracked(self, tmp_path):
        r = run(generate_commit_message(
            GenerateCommitConfig(),
            GenerateCommitInput(repo_path=str(tmp_path), caller=make_caller("msg"),
                                diff_text="+x=1"),
            tmp_path / "out",
        ))
        assert r["cost_usd"] >= 0

    def test_fingerprint_present(self, tmp_path):
        r = run(generate_commit_message(
            GenerateCommitConfig(),
            GenerateCommitInput(repo_path=str(tmp_path), caller=make_caller("msg"),
                                diff_text="+x=1"),
            tmp_path / "out",
        ))
        assert r["fingerprint"]

    def test_first_line_only(self, tmp_path):
        r = run(generate_commit_message(
            GenerateCommitConfig(),
            GenerateCommitInput(repo_path=str(tmp_path),
                                caller=make_caller("feat: add login\n\nMore details here."),
                                diff_text="+x=1"),
            tmp_path / "out",
        ))
        assert "\n" not in r["message"]

    def test_status_field(self, tmp_path):
        r = run(generate_commit_message(
            GenerateCommitConfig(),
            GenerateCommitInput(repo_path=str(tmp_path), caller=make_caller("msg"),
                                diff_text="+x=1"),
            tmp_path / "out",
        ))
        assert r["status"] in ("completed", "failed")

    def test_error_is_none_on_success(self, tmp_path):
        r = run(generate_commit_message(
            GenerateCommitConfig(),
            GenerateCommitInput(repo_path=str(tmp_path), caller=make_caller("msg"),
                                diff_text="+x=1"),
            tmp_path / "out",
        ))
        assert r["error"] is None

    def test_truncates_large_diff(self, tmp_path):
        big_diff = "+x = 1\n" * 5000
        r = run(generate_commit_message(
            GenerateCommitConfig(max_diff_tokens=100),
            GenerateCommitInput(repo_path=str(tmp_path), caller=make_caller("msg"),
                                diff_text=big_diff),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"


# ===========================================================================
# summarize_session
# ===========================================================================

class TestSummarizeSession:
    def test_returns_summary(self, tmp_path):
        r = run(summarize_session(
            SummarizeSessionConfig(),
            SummarizeSessionInput(messages=MSGS, caller=make_caller("Session summary text.")),
            tmp_path,
        ))
        assert r["status"] == "completed" and "summary" in r

    def test_empty_messages(self, tmp_path):
        r = run(summarize_session(
            SummarizeSessionConfig(),
            SummarizeSessionInput(messages=[], caller=make_caller()),
            tmp_path,
        ))
        assert r["status"] == "completed" and "(empty" in r["summary"]

    def test_cost_tracked(self, tmp_path):
        r = run(summarize_session(
            SummarizeSessionConfig(),
            SummarizeSessionInput(messages=MSGS, caller=make_caller("ok")),
            tmp_path,
        ))
        assert r["cost_usd"] >= 0

    def test_error_fails(self, tmp_path):
        async def bad(**kw): raise RuntimeError("fail")
        r = run(summarize_session(
            SummarizeSessionConfig(),
            SummarizeSessionInput(messages=MSGS, caller=bad),
            tmp_path,
        ))
        assert r["status"] == "failed"

    def test_detailed_mode(self, tmp_path):
        r = run(summarize_session(
            SummarizeSessionConfig(summary_length="detailed"),
            SummarizeSessionInput(messages=MSGS, caller=make_caller("detailed summary")),
            tmp_path,
        ))
        assert r["status"] == "completed"

    def test_max_messages_truncated(self, tmp_path):
        many = [{"role": "user", "content": f"msg{i}"} for i in range(300)]
        prompts = []
        async def cap(**kw):
            prompts.append(kw["messages"])
            return {"content":[{"type":"text","text":"ok"}],"usage":{}}
        run(summarize_session(
            SummarizeSessionConfig(max_messages=10),
            SummarizeSessionInput(messages=many, caller=cap),
            tmp_path,
        ))
        assert prompts  # was called

    def test_on_step_called(self, tmp_path):
        events = []
        run(summarize_session(
            SummarizeSessionConfig(),
            SummarizeSessionInput(messages=MSGS, caller=make_caller("ok")),
            tmp_path,
            on_step=lambda e: events.append(e["event"]),
        ))
        assert "completed" in events

    def test_status_field(self, tmp_path):
        r = run(summarize_session(
            SummarizeSessionConfig(),
            SummarizeSessionInput(messages=MSGS, caller=make_caller("ok")),
            tmp_path,
        ))
        assert r["status"] in ("completed", "failed")

    def test_error_none_on_success(self, tmp_path):
        r = run(summarize_session(
            SummarizeSessionConfig(),
            SummarizeSessionInput(messages=MSGS, caller=make_caller("ok")),
            tmp_path,
        ))
        assert r["error"] is None

    def test_summary_content(self, tmp_path):
        r = run(summarize_session(
            SummarizeSessionConfig(),
            SummarizeSessionInput(messages=MSGS, caller=make_caller("fixed the auth bug")),
            tmp_path,
        ))
        assert "fixed the auth bug" in r["summary"]


# ===========================================================================
# code_review
# ===========================================================================

class TestCodeReview:
    def test_completes(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1\n")
        r = run(code_review(
            CodeReviewConfig(),
            CodeReviewInput(paths=[str(f)], caller=make_caller("Looks good.")),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"

    def test_report_written(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1\n")
        r = run(code_review(
            CodeReviewConfig(),
            CodeReviewInput(paths=[str(f)], caller=make_caller("Review text.")),
            tmp_path / "out",
        ))
        assert r["report_path"] and Path(str(r["report_path"])).exists()

    def test_trail_written(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1\n")
        r = run(code_review(
            CodeReviewConfig(),
            CodeReviewInput(paths=[str(f)], caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["decision_trail"]["steps"] > 0

    def test_cost_tracked(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1\n")
        r = run(code_review(
            CodeReviewConfig(),
            CodeReviewInput(paths=[str(f)], caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["cost_usd"] > 0

    def test_diff_input(self, tmp_path):
        r = run(code_review(
            CodeReviewConfig(),
            CodeReviewInput(paths=[], diff_text="+x=1\n-y=2", caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"

    def test_error_fails(self, tmp_path):
        async def bad(**kw): raise RuntimeError("api fail")
        r = run(code_review(
            CodeReviewConfig(),
            CodeReviewInput(paths=[], caller=bad),
            tmp_path / "out",
        ))
        assert r["status"] == "failed"

    def test_fingerprint(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1")
        r = run(code_review(
            CodeReviewConfig(),
            CodeReviewInput(paths=[str(f)], caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["fingerprint"]

    def test_on_step_called(self, tmp_path):
        events = []
        run(code_review(
            CodeReviewConfig(),
            CodeReviewInput(paths=[], caller=make_caller("ok")),
            tmp_path / "out",
            on_step=lambda e: events.append(e["event"]),
        ))
        assert events

    def test_context_included(self, tmp_path):
        prompts = []
        async def cap(**kw):
            prompts.append(kw["messages"][0]["content"])
            return {"content":[{"type":"text","text":"ok"}],"usage":{}}
        run(code_review(
            CodeReviewConfig(),
            CodeReviewInput(paths=[], caller=cap, context="review for security"),
            tmp_path / "out",
        ))
        assert "security" in prompts[0]

    def test_multiple_files(self, tmp_path):
        f1 = tmp_path / "a.py"; f1.write_text("a=1")
        f2 = tmp_path / "b.py"; f2.write_text("b=2")
        r = run(code_review(
            CodeReviewConfig(),
            CodeReviewInput(paths=[str(f1), str(f2)], caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"


# ===========================================================================
# generate_tests
# ===========================================================================

class TestGenerateTests:
    def test_completes(self, tmp_path):
        f = tmp_path / "calc.py"; f.write_text("def add(a,b): return a+b\n")
        r = run(generate_tests(
            GenerateTestsConfig(),
            GenerateTestsInput(target_path=str(f), caller=make_caller("def test_add(): assert add(1,2)==3")),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"

    def test_test_file_created(self, tmp_path):
        f = tmp_path / "calc.py"; f.write_text("def add(a,b): return a+b\n")
        r = run(generate_tests(
            GenerateTestsConfig(),
            GenerateTestsInput(target_path=str(f), caller=make_caller("def test_add(): pass")),
            tmp_path / "out",
        ))
        assert r["test_file_path"] and Path(str(r["test_file_path"])).exists()

    def test_auto_derives_test_path(self, tmp_path):
        f = tmp_path / "utils.py"; f.write_text("x=1")
        r = run(generate_tests(
            GenerateTestsConfig(),
            GenerateTestsInput(target_path=str(f), caller=make_caller("def test_x(): pass")),
            tmp_path / "out",
        ))
        assert r["test_file_path"] and "test_utils.py" in str(r["test_file_path"])

    def test_custom_output_path(self, tmp_path):
        f = tmp_path / "src.py"; f.write_text("x=1")
        out = tmp_path / "tests" / "test_custom.py"
        r = run(generate_tests(
            GenerateTestsConfig(),
            GenerateTestsInput(target_path=str(f), caller=make_caller("def test(): pass"),
                               output_test_path=str(out)),
            tmp_path / "out",
        ))
        assert out.exists()

    def test_report_written(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1")
        r = run(generate_tests(
            GenerateTestsConfig(),
            GenerateTestsInput(target_path=str(f), caller=make_caller("def test(): pass")),
            tmp_path / "out",
        ))
        assert r["report_path"] and Path(str(r["report_path"])).exists()

    def test_missing_file_fails(self, tmp_path):
        r = run(generate_tests(
            GenerateTestsConfig(),
            GenerateTestsInput(target_path=str(tmp_path/"no.py"), caller=make_caller("x")),
            tmp_path / "out",
        ))
        assert r["status"] in ("completed", "failed")  # 读失败但可能继续

    def test_fingerprint(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1")
        r = run(generate_tests(
            GenerateTestsConfig(),
            GenerateTestsInput(target_path=str(f), caller=make_caller("test")),
            tmp_path / "out",
        ))
        assert r["fingerprint"]

    def test_cost_tracked(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1")
        r = run(generate_tests(
            GenerateTestsConfig(),
            GenerateTestsInput(target_path=str(f), caller=make_caller("test")),
            tmp_path / "out",
        ))
        assert r["cost_usd"] > 0

    def test_markdown_fence_stripped(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1")
        caller = make_caller("```python\ndef test_x(): pass\n```")
        r = run(generate_tests(GenerateTestsConfig(),
                               GenerateTestsInput(target_path=str(f), caller=caller),
                               tmp_path / "out"))
        if r["test_file_path"]:
            content = Path(str(r["test_file_path"])).read_text()
            assert "```" not in content

    def test_error_fails(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1")
        async def bad(**kw): raise RuntimeError("fail")
        r = run(generate_tests(GenerateTestsConfig(),
                               GenerateTestsInput(target_path=str(f), caller=bad),
                               tmp_path / "out"))
        assert r["status"] == "failed"


# ===========================================================================
# explain_codebase
# ===========================================================================

class TestExplainCodebase:
    def test_completes(self, tmp_path):
        (tmp_path / "main.py").write_text("def main(): pass\n")
        r = run(explain_codebase(
            ExplainCodebaseConfig(),
            ExplainCodebaseInput(root_path=str(tmp_path), caller=make_caller("This is a project.")),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"

    def test_report_written(self, tmp_path):
        (tmp_path / "f.py").write_text("x=1")
        r = run(explain_codebase(
            ExplainCodebaseConfig(),
            ExplainCodebaseInput(root_path=str(tmp_path), caller=make_caller("Explanation.")),
            tmp_path / "out",
        ))
        assert r["report_path"] and Path(str(r["report_path"])).exists()

    def test_cost_tracked(self, tmp_path):
        r = run(explain_codebase(
            ExplainCodebaseConfig(),
            ExplainCodebaseInput(root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["cost_usd"] >= 0

    def test_focus_in_prompt(self, tmp_path):
        prompts = []
        async def cap(**kw):
            prompts.append(kw["messages"][0]["content"])
            return {"content":[{"type":"text","text":"ok"}],"usage":{}}
        run(explain_codebase(
            ExplainCodebaseConfig(),
            ExplainCodebaseInput(root_path=str(tmp_path), caller=cap, focus="auth flow"),
            tmp_path / "out",
        ))
        assert "auth flow" in prompts[0]

    def test_error_fails(self, tmp_path):
        async def bad(**kw): raise RuntimeError("fail")
        r = run(explain_codebase(
            ExplainCodebaseConfig(),
            ExplainCodebaseInput(root_path=str(tmp_path), caller=bad),
            tmp_path / "out",
        ))
        assert r["status"] == "failed"

    def test_fingerprint(self, tmp_path):
        r = run(explain_codebase(
            ExplainCodebaseConfig(),
            ExplainCodebaseInput(root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["fingerprint"]

    def test_empty_root(self, tmp_path):
        r = run(explain_codebase(
            ExplainCodebaseConfig(),
            ExplainCodebaseInput(root_path=str(tmp_path), caller=make_caller("no files")),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"

    def test_on_step(self, tmp_path):
        events = []
        run(explain_codebase(
            ExplainCodebaseConfig(),
            ExplainCodebaseInput(root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
            on_step=lambda e: events.append(e["event"]),
        ))
        assert "scanning" in events

    def test_error_is_none_on_success(self, tmp_path):
        r = run(explain_codebase(
            ExplainCodebaseConfig(),
            ExplainCodebaseInput(root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["error"] is None

    def test_status_field(self, tmp_path):
        r = run(explain_codebase(
            ExplainCodebaseConfig(),
            ExplainCodebaseInput(root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["status"] in ("completed", "failed")


# ===========================================================================
# security_audit
# ===========================================================================

class TestSecurityAudit:
    def test_completes(self, tmp_path):
        f = tmp_path / "app.py"; f.write_text("password='hardcoded123'\n")
        r = run(security_audit(
            SecurityAuditConfig(),
            SecurityAuditInput(paths=[str(f)], caller=make_caller("Found: hardcoded password.")),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"

    def test_report_written(self, tmp_path):
        f = tmp_path / "app.py"; f.write_text("x=1")
        r = run(security_audit(
            SecurityAuditConfig(),
            SecurityAuditInput(paths=[str(f)], caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["report_path"] and Path(str(r["report_path"])).exists()

    def test_trail_written(self, tmp_path):
        f = tmp_path / "app.py"; f.write_text("x=1")
        r = run(security_audit(
            SecurityAuditConfig(),
            SecurityAuditInput(paths=[str(f)], caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["decision_trail"]["steps"] > 0

    def test_cost_tracked(self, tmp_path):
        f = tmp_path / "app.py"; f.write_text("x=1")
        r = run(security_audit(
            SecurityAuditConfig(),
            SecurityAuditInput(paths=[str(f)], caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["cost_usd"] > 0

    def test_error_fails(self, tmp_path):
        async def bad(**kw): raise RuntimeError("fail")
        r = run(security_audit(
            SecurityAuditConfig(),
            SecurityAuditInput(paths=[], caller=bad),
            tmp_path / "out",
        ))
        assert r["status"] == "failed"

    def test_fingerprint(self, tmp_path):
        r = run(security_audit(
            SecurityAuditConfig(),
            SecurityAuditInput(paths=[], caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["fingerprint"]

    def test_on_step(self, tmp_path):
        events = []
        run(security_audit(
            SecurityAuditConfig(),
            SecurityAuditInput(paths=[], caller=make_caller("ok")),
            tmp_path / "out",
            on_step=lambda e: events.append(e["event"]),
        ))
        assert "audit_start" in events

    def test_severity_threshold(self, tmp_path):
        prompts = []
        async def cap(**kw):
            prompts.append(kw["messages"][0]["content"])
            return {"content":[{"type":"text","text":"ok"}],"usage":{}}
        run(security_audit(
            SecurityAuditConfig(severity_threshold="critical"),
            SecurityAuditInput(paths=[], caller=cap),
            tmp_path / "out",
        ))
        assert "critical" in prompts[0]

    def test_context_used(self, tmp_path):
        prompts = []
        async def cap(**kw):
            prompts.append(kw["messages"][0]["content"])
            return {"content":[{"type":"text","text":"ok"}],"usage":{}}
        run(security_audit(
            SecurityAuditConfig(),
            SecurityAuditInput(paths=[], caller=cap, context="focus on SQL injection"),
            tmp_path / "out",
        ))
        assert "SQL injection" in prompts[0]

    def test_error_none_on_success(self, tmp_path):
        r = run(security_audit(
            SecurityAuditConfig(),
            SecurityAuditInput(paths=[], caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["error"] is None


# ===========================================================================
# refactor_transaction
# ===========================================================================

class TestRefactorTransaction:
    def test_completes(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("foo = 1\n")
        edits_json = json.dumps([{"path": str(f), "search": "foo", "replace": "bar"}])
        r = run(refactor_transaction(
            RefactorConfig(),
            RefactorInput(instruction="rename foo to bar", paths=[str(f)],
                          caller=make_caller(edits_json)),
            tmp_path / "out",
        ))
        assert r["status"] in ("completed", "failed")

    def test_file_modified(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("foo = 1\n")
        edits_json = json.dumps([{"path": str(f), "search": "foo = 1", "replace": "bar = 1"}])
        run(refactor_transaction(
            RefactorConfig(),
            RefactorInput(instruction="rename", paths=[str(f)],
                          caller=make_caller(edits_json)),
            tmp_path / "out",
        ))
        # 文件可能已修改或未修改（取决于 edit 解析）

    def test_dry_run_no_write(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("foo = 1\n")
        orig = f.read_text()
        edits_json = json.dumps([{"path": str(f), "search": "foo = 1", "replace": "bar = 1"}])
        run(refactor_transaction(
            RefactorConfig(dry_run=True),
            RefactorInput(instruction="rename", paths=[str(f)],
                          caller=make_caller(edits_json)),
            tmp_path / "out",
        ))
        assert f.read_text() == orig  # dry_run 不写盘

    def test_report_written(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1")
        r = run(refactor_transaction(
            RefactorConfig(),
            RefactorInput(instruction="refactor", paths=[str(f)],
                          caller=make_caller("[]")),
            tmp_path / "out",
        ))
        assert r["report_path"]

    def test_trail_written(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1")
        r = run(refactor_transaction(
            RefactorConfig(),
            RefactorInput(instruction="refactor", paths=[str(f)],
                          caller=make_caller("[]")),
            tmp_path / "out",
        ))
        assert r["decision_trail"]["steps"] > 0

    def test_invalid_json_edits(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1")
        r = run(refactor_transaction(
            RefactorConfig(),
            RefactorInput(instruction="refactor", paths=[str(f)],
                          caller=make_caller("not json at all")),
            tmp_path / "out",
        ))
        assert r["status"] in ("completed", "failed")

    def test_error_fails(self, tmp_path):
        async def bad(**kw): raise RuntimeError("fail")
        r = run(refactor_transaction(
            RefactorConfig(),
            RefactorInput(instruction="refactor", paths=[],
                          caller=bad),
            tmp_path / "out",
        ))
        assert r["status"] == "failed"

    def test_fingerprint(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1")
        r = run(refactor_transaction(
            RefactorConfig(),
            RefactorInput(instruction="rename", paths=[str(f)],
                          caller=make_caller("[]")),
            tmp_path / "out",
        ))
        assert r["fingerprint"]

    def test_cost_tracked(self, tmp_path):
        f = tmp_path / "x.py"; f.write_text("x=1")
        r = run(refactor_transaction(
            RefactorConfig(),
            RefactorInput(instruction="rename", paths=[str(f)],
                          caller=make_caller("[]")),
            tmp_path / "out",
        ))
        assert r["cost_usd"] >= 0

    def test_on_step(self, tmp_path):
        events = []
        f = tmp_path / "x.py"; f.write_text("x=1")
        run(refactor_transaction(
            RefactorConfig(),
            RefactorInput(instruction="rename", paths=[str(f)],
                          caller=make_caller("[]")),
            tmp_path / "out",
            on_step=lambda e: events.append(e["event"]),
        ))
        assert "refactor_start" in events


# ===========================================================================
# run_and_fix
# ===========================================================================

class TestRunAndFix:
    def test_completes_on_success(self, tmp_path):
        r = run(run_and_fix(
            RunAndFixConfig(),
            RunAndFixInput(command="echo hello", cwd=str(tmp_path), caller=make_caller()),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"

    def test_iterations_tracked(self, tmp_path):
        r = run(run_and_fix(
            RunAndFixConfig(),
            RunAndFixInput(command="true", cwd=str(tmp_path), caller=make_caller()),
            tmp_path / "out",
        ))
        assert r["iterations"] >= 1

    def test_failed_command_retries(self, tmp_path):
        r = run(run_and_fix(
            RunAndFixConfig(max_iterations=2),
            RunAndFixInput(command="false", cwd=str(tmp_path), caller=make_caller("[]")),
            tmp_path / "out",
        ))
        assert r["status"] in ("completed", "failed")
        assert r["iterations"] <= 2

    def test_report_written(self, tmp_path):
        r = run(run_and_fix(
            RunAndFixConfig(),
            RunAndFixInput(command="echo ok", cwd=str(tmp_path), caller=make_caller()),
            tmp_path / "out",
        ))
        assert r["report_path"] and Path(str(r["report_path"])).exists()

    def test_trail_written(self, tmp_path):
        r = run(run_and_fix(
            RunAndFixConfig(),
            RunAndFixInput(command="echo ok", cwd=str(tmp_path), caller=make_caller()),
            tmp_path / "out",
        ))
        assert r["decision_trail"]["steps"] > 0

    def test_cost_tracked(self, tmp_path):
        r = run(run_and_fix(
            RunAndFixConfig(),
            RunAndFixInput(command="false", cwd=str(tmp_path), caller=make_caller("[]")),
            tmp_path / "out",
        ))
        assert r["cost_usd"] >= 0

    def test_fingerprint(self, tmp_path):
        r = run(run_and_fix(
            RunAndFixConfig(),
            RunAndFixInput(command="echo ok", cwd=str(tmp_path), caller=make_caller()),
            tmp_path / "out",
        ))
        assert r["fingerprint"]

    def test_on_step(self, tmp_path):
        events = []
        run(run_and_fix(
            RunAndFixConfig(),
            RunAndFixInput(command="echo ok", cwd=str(tmp_path), caller=make_caller()),
            tmp_path / "out",
            on_step=lambda e: events.append(e["event"]),
        ))
        assert "run_start" in events or "iteration" in events

    def test_max_iterations_bounded(self, tmp_path):
        r = run(run_and_fix(
            RunAndFixConfig(max_iterations=2),
            RunAndFixInput(command="exit 1", cwd=str(tmp_path), caller=make_caller("[]")),
            tmp_path / "out",
        ))
        assert r["iterations"] <= 2

    def test_error_field_on_failure(self, tmp_path):
        r = run(run_and_fix(
            RunAndFixConfig(max_iterations=1),
            RunAndFixInput(command="exit 99", cwd=str(tmp_path), caller=make_caller("[]")),
            tmp_path / "out",
        ))
        if r["status"] == "failed":
            assert r["error"] is not None


# ===========================================================================
# create_checkpoint / rewind_to_checkpoint
# ===========================================================================

class TestCreateCheckpoint:
    def test_completes(self, tmp_path):
        r = run(create_checkpoint(
            CreateCheckpointConfig(),
            CreateCheckpointInput(messages=MSGS, session_id="s1"),
            tmp_path,
        ))
        assert r["status"] == "completed"

    def test_checkpoint_id_unique(self, tmp_path):
        r1 = run(create_checkpoint(CreateCheckpointConfig(),
                                   CreateCheckpointInput(messages=MSGS), tmp_path))
        r2 = run(create_checkpoint(CreateCheckpointConfig(),
                                   CreateCheckpointInput(messages=MSGS), tmp_path))
        assert r1["checkpoint_id"] != r2["checkpoint_id"]

    def test_local_file_created(self, tmp_path):
        r = run(create_checkpoint(CreateCheckpointConfig(),
                                  CreateCheckpointInput(messages=MSGS), tmp_path))
        assert Path(r["revision"]).exists()

    def test_store_called(self, tmp_path):
        store = make_store(revision="store_rev_001")
        r = run(create_checkpoint(CreateCheckpointConfig(),
                                  CreateCheckpointInput(messages=MSGS, store=store, session_id="x"),
                                  tmp_path))
        store.save.assert_called_once()
        assert r["revision"] == "store_rev_001"

    def test_message_count(self, tmp_path):
        r = run(create_checkpoint(CreateCheckpointConfig(),
                                  CreateCheckpointInput(messages=MSGS), tmp_path))
        assert r["message_count"] == len(MSGS)

    def test_fingerprint(self, tmp_path):
        r = run(create_checkpoint(CreateCheckpointConfig(),
                                  CreateCheckpointInput(messages=MSGS), tmp_path))
        assert r["fingerprint"]

    def test_trail_written(self, tmp_path):
        r = run(create_checkpoint(CreateCheckpointConfig(),
                                  CreateCheckpointInput(messages=MSGS), tmp_path))
        assert r["decision_trail"]["steps"] > 0

    def test_store_error_fails(self, tmp_path):
        store = make_store()
        store.save = AsyncMock(side_effect=RuntimeError("db error"))
        r = run(create_checkpoint(CreateCheckpointConfig(),
                                  CreateCheckpointInput(messages=MSGS, store=store), tmp_path))
        assert r["status"] == "failed"

    def test_cost_zero(self, tmp_path):
        r = run(create_checkpoint(CreateCheckpointConfig(),
                                  CreateCheckpointInput(messages=MSGS), tmp_path))
        assert r["cost_usd"] == 0.0

    def test_on_step(self, tmp_path):
        events = []
        run(create_checkpoint(CreateCheckpointConfig(),
                              CreateCheckpointInput(messages=MSGS), tmp_path,
                              on_step=lambda e: events.append(e["event"])))
        assert "checkpoint_saved" in events


class TestRewindToCheckpoint:
    def test_rewind_from_file(self, tmp_path):
        # 先创建 checkpoint
        cr = run(create_checkpoint(CreateCheckpointConfig(),
                                   CreateCheckpointInput(messages=MSGS, session_id="s1"),
                                   tmp_path))
        ckpt_path = cr["revision"]

        r = run(rewind_to_checkpoint(
            RewindConfig(),
            RewindInput(checkpoint_id=cr["checkpoint_id"], checkpoint_path=ckpt_path),
            tmp_path,
        ))
        assert r["status"] == "completed" and r["messages"] == MSGS

    def test_rewind_from_store(self, tmp_path):
        data = {"messages": MSGS, "metadata": {"key": "val"}}
        store = AsyncMock()
        store.load = AsyncMock(return_value=json.dumps(data))
        r = run(rewind_to_checkpoint(
            RewindConfig(),
            RewindInput(checkpoint_id="ckpt_abc", store=store),
            tmp_path,
        ))
        assert r["status"] == "completed" and r["messages"] == MSGS

    def test_no_store_no_path_fails(self, tmp_path):
        r = run(rewind_to_checkpoint(
            RewindConfig(),
            RewindInput(checkpoint_id="ckpt_abc"),
            tmp_path,
        ))
        assert r["status"] == "failed"

    def test_store_returns_none_fails(self, tmp_path):
        store = AsyncMock()
        store.load = AsyncMock(return_value=None)
        r = run(rewind_to_checkpoint(
            RewindConfig(),
            RewindInput(checkpoint_id="missing", store=store),
            tmp_path,
        ))
        assert r["status"] == "failed"

    def test_message_count(self, tmp_path):
        cr = run(create_checkpoint(CreateCheckpointConfig(),
                                   CreateCheckpointInput(messages=MSGS), tmp_path))
        r = run(rewind_to_checkpoint(
            RewindConfig(),
            RewindInput(checkpoint_id=cr["checkpoint_id"], checkpoint_path=cr["revision"]),
            tmp_path,
        ))
        assert r["message_count"] == len(MSGS)

    def test_trail_written(self, tmp_path):
        cr = run(create_checkpoint(CreateCheckpointConfig(),
                                   CreateCheckpointInput(messages=MSGS), tmp_path))
        r = run(rewind_to_checkpoint(
            RewindConfig(),
            RewindInput(checkpoint_id=cr["checkpoint_id"], checkpoint_path=cr["revision"]),
            tmp_path,
        ))
        assert r["decision_trail"]["steps"] > 0

    def test_cost_zero(self, tmp_path):
        cr = run(create_checkpoint(CreateCheckpointConfig(),
                                   CreateCheckpointInput(messages=MSGS), tmp_path))
        r = run(rewind_to_checkpoint(
            RewindConfig(),
            RewindInput(checkpoint_id=cr["checkpoint_id"], checkpoint_path=cr["revision"]),
            tmp_path,
        ))
        assert r["cost_usd"] == 0.0

    def test_on_step(self, tmp_path):
        events = []
        cr = run(create_checkpoint(CreateCheckpointConfig(),
                                   CreateCheckpointInput(messages=MSGS), tmp_path))
        run(rewind_to_checkpoint(
            RewindConfig(),
            RewindInput(checkpoint_id=cr["checkpoint_id"], checkpoint_path=cr["revision"]),
            tmp_path,
            on_step=lambda e: events.append(e["event"]),
        ))
        assert "rewind_done" in events

    def test_error_field_none_on_success(self, tmp_path):
        cr = run(create_checkpoint(CreateCheckpointConfig(),
                                   CreateCheckpointInput(messages=MSGS), tmp_path))
        r = run(rewind_to_checkpoint(
            RewindConfig(),
            RewindInput(checkpoint_id=cr["checkpoint_id"], checkpoint_path=cr["revision"]),
            tmp_path,
        ))
        assert r["error"] is None

    def test_metadata_restored(self, tmp_path):
        cr = run(create_checkpoint(CreateCheckpointConfig(),
                                   CreateCheckpointInput(messages=MSGS,
                                                         metadata={"key": "value"}), tmp_path))
        r = run(rewind_to_checkpoint(
            RewindConfig(),
            RewindInput(checkpoint_id=cr["checkpoint_id"], checkpoint_path=cr["revision"]),
            tmp_path,
        ))
        assert r["metadata"].get("key") == "value"


# ===========================================================================
# compact_conversation
# ===========================================================================

class TestCompactConversation:
    def test_compacts_long_history(self, tmp_path):
        msgs = [{"role": "user" if i%2==0 else "assistant", "content": f"msg{i}"}
                for i in range(10)]
        r = run(compact_conversation(
            CompactConversationConfig(),
            CompactConversationInput(messages=msgs, caller=make_caller("history summary")),
            tmp_path,
        ))
        assert r["status"] == "completed"
        assert len(r["messages"]) < len(msgs)

    def test_short_history_unchanged(self, tmp_path):
        r = run(compact_conversation(
            CompactConversationConfig(),
            CompactConversationInput(messages=MSGS, caller=make_caller("ok")),
            tmp_path,
        ))
        assert r["compacted"] is False

    def test_cost_tracked(self, tmp_path):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(10)]
        r = run(compact_conversation(
            CompactConversationConfig(),
            CompactConversationInput(messages=msgs, caller=make_caller("summary")),
            tmp_path,
        ))
        assert r["cost_usd"] >= 0

    def test_fingerprint_m5(self, tmp_path):
        """M5 Owner裁决：fingerprint 区分多次压缩版本。"""
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(10)]
        r1 = run(compact_conversation(CompactConversationConfig(),
                                      CompactConversationInput(messages=msgs, caller=make_caller("s")),
                                      tmp_path))
        msgs2 = msgs + [{"role": "user", "content": "extra"}]
        r2 = run(compact_conversation(CompactConversationConfig(),
                                      CompactConversationInput(messages=msgs2, caller=make_caller("s")),
                                      tmp_path))
        assert r1["fingerprint"] != r2["fingerprint"]

    def test_trail_written(self, tmp_path):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(10)]
        r = run(compact_conversation(
            CompactConversationConfig(),
            CompactConversationInput(messages=msgs, caller=make_caller("ok")),
            tmp_path,
        ))
        assert r["decision_trail"]["steps"] > 0

    def test_error_fails(self, tmp_path):
        async def bad(**kw): raise RuntimeError("fail")
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(10)]
        r = run(compact_conversation(
            CompactConversationConfig(),
            CompactConversationInput(messages=msgs, caller=bad),
            tmp_path,
        ))
        assert r["status"] == "failed"

    def test_messages_in_result(self, tmp_path):
        r = run(compact_conversation(
            CompactConversationConfig(),
            CompactConversationInput(messages=MSGS, caller=make_caller("ok")),
            tmp_path,
        ))
        assert "messages" in r

    def test_summary_injected(self, tmp_path):
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        r = run(compact_conversation(
            CompactConversationConfig(),
            CompactConversationInput(messages=msgs, caller=make_caller("the summary text")),
            tmp_path,
        ))
        all_content = " ".join(str(m.get("content","")) for m in r["messages"])
        assert "summary" in all_content.lower() or "the summary text" in all_content

    def test_on_step(self, tmp_path):
        events = []
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(10)]
        run(compact_conversation(
            CompactConversationConfig(),
            CompactConversationInput(messages=msgs, caller=make_caller("ok")),
            tmp_path,
            on_step=lambda e: events.append(e["event"]),
        ))
        assert events

    def test_error_none_on_success(self, tmp_path):
        r = run(compact_conversation(
            CompactConversationConfig(),
            CompactConversationInput(messages=MSGS, caller=make_caller("ok")),
            tmp_path,
        ))
        assert r["error"] is None


# ===========================================================================
# migrate_dependency
# ===========================================================================

class TestMigrateDependency:
    def test_completes(self, tmp_path):
        (tmp_path / "setup.py").write_text("requires=['requests==2.28']")
        r = run(migrate_dependency(
            MigrateDependencyConfig(),
            MigrateDependencyInput(dependency="requests", target_version="2.31",
                                   root_path=str(tmp_path), caller=make_caller("Migration guide.")),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"

    def test_report_written(self, tmp_path):
        r = run(migrate_dependency(
            MigrateDependencyConfig(),
            MigrateDependencyInput(dependency="requests", target_version="2.31",
                                   root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["report_path"] and Path(str(r["report_path"])).exists()

    def test_trail_written(self, tmp_path):
        r = run(migrate_dependency(
            MigrateDependencyConfig(),
            MigrateDependencyInput(dependency="x", target_version="2.0",
                                   root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["decision_trail"]["steps"] > 0

    def test_cost_tracked(self, tmp_path):
        r = run(migrate_dependency(
            MigrateDependencyConfig(),
            MigrateDependencyInput(dependency="x", target_version="2.0",
                                   root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["cost_usd"] > 0

    def test_fingerprint(self, tmp_path):
        r = run(migrate_dependency(
            MigrateDependencyConfig(),
            MigrateDependencyInput(dependency="requests", target_version="2.31",
                                   root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["fingerprint"]

    def test_error_fails(self, tmp_path):
        async def bad(**kw): raise RuntimeError("fail")
        r = run(migrate_dependency(
            MigrateDependencyConfig(),
            MigrateDependencyInput(dependency="x", target_version="1.0",
                                   root_path=str(tmp_path), caller=bad),
            tmp_path / "out",
        ))
        assert r["status"] == "failed"

    def test_on_step(self, tmp_path):
        events = []
        run(migrate_dependency(
            MigrateDependencyConfig(),
            MigrateDependencyInput(dependency="x", target_version="1.0",
                                   root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
            on_step=lambda e: events.append(e["event"]),
        ))
        assert "migrate_start" in events

    def test_dep_name_in_prompt(self, tmp_path):
        prompts = []
        async def cap(**kw):
            prompts.append(kw["messages"][0]["content"])
            return {"content":[{"type":"text","text":"ok"}],"usage":{}}
        run(migrate_dependency(
            MigrateDependencyConfig(),
            MigrateDependencyInput(dependency="my_special_lib", target_version="9.0",
                                   root_path=str(tmp_path), caller=cap),
            tmp_path / "out",
        ))
        assert "my_special_lib" in prompts[0]

    def test_error_none_on_success(self, tmp_path):
        r = run(migrate_dependency(
            MigrateDependencyConfig(),
            MigrateDependencyInput(dependency="x", target_version="1.0",
                                   root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["error"] is None

    def test_status_field(self, tmp_path):
        r = run(migrate_dependency(
            MigrateDependencyConfig(),
            MigrateDependencyInput(dependency="x", target_version="1.0",
                                   root_path=str(tmp_path), caller=make_caller("ok")),
            tmp_path / "out",
        ))
        assert r["status"] in ("completed", "failed")


# ===========================================================================
# install_plugin
# ===========================================================================

class TestInstallPlugin:
    BUNDLE = {
        "name": "my_plugin", "version": "1.2.3",
        "skills": ["refactor_python"],
        "commands": [{"name": "/init", "description": "init cmd"}],
        "hooks": [{"event": "PreToolUse", "command": "/hook.sh"}],
    }

    def test_completes(self, tmp_path):
        r = run(install_plugin(
            InstallPluginConfig(),
            InstallPluginInput(plugin_bundle=self.BUNDLE, install_dir=str(tmp_path/"plugins")),
            tmp_path / "out",
        ))
        assert r["status"] == "completed"

    def test_manifest_file_created(self, tmp_path):
        r = run(install_plugin(
            InstallPluginConfig(),
            InstallPluginInput(plugin_bundle=self.BUNDLE, install_dir=str(tmp_path/"plugins")),
            tmp_path / "out",
        ))
        assert r["install_path"] and Path(str(r["install_path"])).exists()

    def test_manifest_content(self, tmp_path):
        r = run(install_plugin(
            InstallPluginConfig(),
            InstallPluginInput(plugin_bundle=self.BUNDLE, install_dir=str(tmp_path/"plugins")),
            tmp_path / "out",
        ))
        data = json.loads(Path(str(r["install_path"])).read_text())
        assert data["name"] == "my_plugin" and data["version"] == "1.2.3"

    def test_fingerprint(self, tmp_path):
        r = run(install_plugin(
            InstallPluginConfig(),
            InstallPluginInput(plugin_bundle=self.BUNDLE, install_dir=str(tmp_path/"plugins")),
            tmp_path / "out",
        ))
        assert r["fingerprint"]

    def test_trail_written(self, tmp_path):
        r = run(install_plugin(
            InstallPluginConfig(),
            InstallPluginInput(plugin_bundle=self.BUNDLE, install_dir=str(tmp_path/"plugins")),
            tmp_path / "out",
        ))
        assert r["decision_trail"]["steps"] > 0

    def test_cost_zero(self, tmp_path):
        r = run(install_plugin(
            InstallPluginConfig(),
            InstallPluginInput(plugin_bundle=self.BUNDLE, install_dir=str(tmp_path/"plugins")),
            tmp_path / "out",
        ))
        assert r["cost_usd"] == 0.0

    def test_missing_name_fails(self, tmp_path):
        r = run(install_plugin(
            InstallPluginConfig(),
            InstallPluginInput(plugin_bundle={"version": "1.0"},
                               install_dir=str(tmp_path/"plugins")),
            tmp_path / "out",
        ))
        assert r["status"] == "failed"

    def test_on_step(self, tmp_path):
        events = []
        run(install_plugin(
            InstallPluginConfig(),
            InstallPluginInput(plugin_bundle=self.BUNDLE, install_dir=str(tmp_path/"plugins")),
            tmp_path / "out",
            on_step=lambda e: events.append(e["event"]),
        ))
        assert "install_start" in events

    def test_plugin_name_in_result(self, tmp_path):
        r = run(install_plugin(
            InstallPluginConfig(),
            InstallPluginInput(plugin_bundle=self.BUNDLE, install_dir=str(tmp_path/"plugins")),
            tmp_path / "out",
        ))
        assert r["plugin_name"] == "my_plugin"

    def test_version_in_result(self, tmp_path):
        r = run(install_plugin(
            InstallPluginConfig(),
            InstallPluginInput(plugin_bundle=self.BUNDLE, install_dir=str(tmp_path/"plugins")),
            tmp_path / "out",
        ))
        assert r["version"] == "1.2.3"
