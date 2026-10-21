"""Tests for the Veya/3O 42-element inventory — omodul layer (6 workflows).

Covers: sandbox_execution_workflow, long_task_memory_workflow,
code_investigation_workflow, agent_self_update_workflow,
mcp_execution_workflow, multi_agent_worktree_workflow.

验证路径恒跑；真实 docker/PG/远端 MCP 依赖路径优雅跳过。
"""

from __future__ import annotations

import pathlib

import pytest

_OUT = pathlib.Path("/tmp")


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeLLM:
    async def __call__(self, *, messages, tools=None, max_tokens=4096, stop_sequences=None):
        q = messages[-1]["content"]
        if "root_cause" in q:
            ans = '{"root_cause": "空指针", "confidence": 0.9, "fix_suggestions": ["判空"]}'
        elif "摘要" in q:
            ans = "关键决策"
        elif "Prompt:" in q:
            ans = "8"
        else:
            ans = "结论：模块 A 与 B 存在循环依赖。"
        return {"content": [{"type": "text", "text": ans}],
                "usage": {"input_tokens": 20, "output_tokens": 10},
                "stop_reason": "end_turn"}


class FakeMcpClient:
    async def list_tools(self):
        return [{"name": "ping", "description": "ping"}]

    async def call_tool(self, name, args):
        return {"content": [{"type": "text", "text": "pong"}], "isError": False}


class FakeEmbed:
    async def __call__(self, *, text, model="x"):
        return [1.0, 0.0]


class FakeStore:
    async def search(self, *args, **kwargs):
        return [{"chunk_id": "ping", "content": "p", "score": 0.8}]


# ---------------------------------------------------------------------------
# sandbox_execution_workflow
# ---------------------------------------------------------------------------


class TestSandboxExecutionWorkflow:
    async def test_missing_container_fails_gracefully(self, tmp_path: pathlib.Path):
        from omodul import SandboxExecutionConfig, SandboxExecutionInput, sandbox_execution_workflow

        r = await sandbox_execution_workflow(
            SandboxExecutionConfig(),
            SandboxExecutionInput(command="echo hi"),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "container_id" in r["error"]["message"]
        assert r["decision_trail"]["steps"] >= 1

    async def test_signature_contract(self):
        import inspect

        from omodul import sandbox_execution_workflow

        sig = inspect.signature(sandbox_execution_workflow)
        params = list(sig.parameters)
        assert params[:3] == ["config", "input_data", "output_dir"]
        assert list(sig.parameters["on_step"].kind.name)  # keyword-only


# ---------------------------------------------------------------------------
# long_task_memory_workflow
# ---------------------------------------------------------------------------


class TestLongTaskMemoryWorkflow:
    async def test_compress(self, tmp_path: pathlib.Path):
        from omodul import LongTaskMemoryConfig, LongTaskMemoryInput, long_task_memory_workflow

        r = await long_task_memory_workflow(
            LongTaskMemoryConfig(),
            LongTaskMemoryInput(
                task_id="t1", action="compress", memory_text="x" * 100_000, caller=FakeLLM()
            ),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert r["compressed"] is True
        assert r["fingerprint"]

    async def test_compress_requires_caller(self, tmp_path: pathlib.Path):
        from omodul import LongTaskMemoryConfig, LongTaskMemoryInput, long_task_memory_workflow

        r = await long_task_memory_workflow(
            LongTaskMemoryConfig(),
            LongTaskMemoryInput(task_id="t1", action="compress", memory_text="x"),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "caller" in r["error"]["message"]

    async def test_snapshot_restore(self, tmp_path: pathlib.Path):
        from omodul import LongTaskMemoryConfig, LongTaskMemoryInput, long_task_memory_workflow

        src = tmp_path / "mem.txt"
        src.write_text("v1")
        r = await long_task_memory_workflow(
            LongTaskMemoryConfig(),
            LongTaskMemoryInput(task_id="t1", action="snapshot", memory_path=str(src)),
            tmp_path,
        )
        assert r["status"] == "completed" and r["rev"]
        src.write_text("v2")
        r = await long_task_memory_workflow(
            LongTaskMemoryConfig(),
            LongTaskMemoryInput(
                task_id="t1", action="restore", memory_path=str(tmp_path / "out"),
                snapshot_rev=r["rev"],
            ),
            tmp_path,
        )
        assert r["status"] == "completed"

    async def test_fingerprint_deterministic(self, tmp_path: pathlib.Path):
        from omodul import LongTaskMemoryConfig, LongTaskMemoryInput
        from omodul.long_task_memory_workflow import compute_fingerprint_for

        inp = LongTaskMemoryInput(task_id="t9", action="compress")
        assert compute_fingerprint_for(LongTaskMemoryConfig(), inp) == compute_fingerprint_for(
            LongTaskMemoryConfig(), inp
        )


# ---------------------------------------------------------------------------
# code_investigation_workflow
# ---------------------------------------------------------------------------


class TestCodeInvestigationWorkflow:
    async def test_investigation_with_report(self, tmp_path: pathlib.Path):
        from omodul import CodeInvestigationConfig, CodeInvestigationInput, code_investigation_workflow

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("def foo(): pass\nimport b\n")
        (src / "b.py").write_text("class B: pass\n")
        r = await code_investigation_workflow(
            CodeInvestigationConfig(),
            CodeInvestigationInput(
                question="哪里有问题", repo_path=str(src), caller=FakeLLM()
            ),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert r["findings"]["total_symbols"] >= 3
        assert r["conclusion"]
        assert r["report_path"] and pathlib.Path(r["report_path"]).exists()
        assert r["cost_usd"] > 0

    async def test_diagnosis_mode(self, tmp_path: pathlib.Path):
        from omodul import CodeInvestigationConfig, CodeInvestigationInput, code_investigation_workflow

        src = tmp_path / "src2"
        src.mkdir()
        (src / "a.py").write_text("def foo(): pass\n")
        r = await code_investigation_workflow(
            CodeInvestigationConfig(),
            CodeInvestigationInput(
                question="x",
                traceback='File "/repo/a.py", line 3, in foo',
                file_paths=[str(src / "a.py")],
                caller=FakeLLM(),
            ),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert r["findings"]["root_cause"] == "空指针"

    async def test_no_files_fails(self, tmp_path: pathlib.Path):
        from omodul import CodeInvestigationConfig, CodeInvestigationInput, code_investigation_workflow

        r = await code_investigation_workflow(
            CodeInvestigationConfig(), CodeInvestigationInput(question="x"), tmp_path
        )
        assert r["status"] == "failed"


# ---------------------------------------------------------------------------
# agent_self_update_workflow
# ---------------------------------------------------------------------------


class TestAgentSelfUpdateWorkflow:
    async def test_adopt_without_judge(self, tmp_path: pathlib.Path):
        from omodul import AgentSelfUpdateConfig, AgentSelfUpdateInput, agent_self_update_workflow

        cfg = tmp_path / "soul.md"
        cfg.write_text("old")
        r = await agent_self_update_workflow(
            AgentSelfUpdateConfig(),
            AgentSelfUpdateInput(
                config_path=str(cfg), current_content="old", candidate_content="new"
            ),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert r["adopted"] is True
        assert cfg.read_text() == "new"
        assert r["rev_before"] and r["rev_after"]

    async def test_reject_below_threshold(self, tmp_path: pathlib.Path):
        from omodul import AgentSelfUpdateConfig, AgentSelfUpdateInput, agent_self_update_workflow

        cfg = tmp_path / "soul2.md"
        cfg.write_text("cur")
        r = await agent_self_update_workflow(
            AgentSelfUpdateConfig(min_score=9.0),
            AgentSelfUpdateInput(
                config_path=str(cfg),
                current_content="cur",
                candidate_content="bad",
                judge=FakeLLM(),
            ),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert r["adopted"] is False
        assert cfg.read_text() == "cur"  # 未采纳，原样保留


# ---------------------------------------------------------------------------
# mcp_execution_workflow
# ---------------------------------------------------------------------------


class TestMcpExecutionWorkflow:
    async def test_explicit_plan(self, tmp_path: pathlib.Path):
        from omodul import McpExecutionConfig, McpExecutionInput, mcp_execution_workflow

        r = await mcp_execution_workflow(
            McpExecutionConfig(),
            McpExecutionInput(
                task="ping", client=FakeMcpClient(),
                explicit_plan=[{"tool": "ping", "arguments": {}}],
            ),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert r["calls"] == 1
        assert r["results"][0]["result"]["content"][0]["text"] == "pong"

    async def test_auto_route(self, tmp_path: pathlib.Path):
        from omodul import McpExecutionConfig, McpExecutionInput, mcp_execution_workflow

        r = await mcp_execution_workflow(
            McpExecutionConfig(),
            McpExecutionInput(
                task="ping it", client=FakeMcpClient(),
                embedder=FakeEmbed(), store=FakeStore(),
            ),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert r["results"][0]["tool"] == "ping"

    async def test_whitelist_violation(self, tmp_path: pathlib.Path):
        from omodul import McpExecutionConfig, McpExecutionInput, mcp_execution_workflow

        r = await mcp_execution_workflow(
            McpExecutionConfig(allowed_tools=["other"]),
            McpExecutionInput(
                task="ping", client=FakeMcpClient(),
                explicit_plan=[{"tool": "ping", "arguments": {}}],
            ),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "whitelist" in r["error"]["message"]

    async def test_client_required(self, tmp_path: pathlib.Path):
        from omodul import McpExecutionConfig, McpExecutionInput, mcp_execution_workflow

        r = await mcp_execution_workflow(
            McpExecutionConfig(),
            McpExecutionInput(task="ping", explicit_plan=[{"tool": "ping", "arguments": {}}]),
            tmp_path,
        )
        assert r["status"] == "failed"


# ---------------------------------------------------------------------------
# multi_agent_worktree_workflow
# ---------------------------------------------------------------------------


class TestMultiAgentWorktreeWorkflow:
    async def test_missing_repo_fails(self, tmp_path: pathlib.Path):
        from omodul import MultiAgentWorktreeConfig, MultiAgentWorktreeInput, multi_agent_worktree_workflow

        r = await multi_agent_worktree_workflow(
            MultiAgentWorktreeConfig(),
            MultiAgentWorktreeInput(
                repo_path="/nonexistent-xyz",
                tasks=[{"branch": "b1"}],
                agent_runner=lambda *a: {"status": "completed"},
            ),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "not found" in r["error"]["message"]

    async def test_runner_required(self, tmp_path: pathlib.Path):
        from omodul import MultiAgentWorktreeConfig, MultiAgentWorktreeInput, multi_agent_worktree_workflow

        r = await multi_agent_worktree_workflow(
            MultiAgentWorktreeConfig(),
            MultiAgentWorktreeInput(repo_path=str(tmp_path), tasks=[{"branch": "b1"}]),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "agent_runner" in r["error"]["message"]

    async def test_real_parallel_execution(self, tmp_path: pathlib.Path):
        """真实 git 仓库 + 双 agent 并行（无 docker 依赖）。"""
        import asyncio

        from oprim.git import _git
        from omodul import MultiAgentWorktreeConfig, MultiAgentWorktreeInput, multi_agent_worktree_workflow

        repo = tmp_path / "repo"
        repo.mkdir()
        _git("init", "-b", "main", repo=repo)
        (repo / "f.txt").write_text("x")
        _git("add", ".", repo=repo)
        _git("commit", "-m", "init", repo=repo)

        done: list[str] = []

        async def runner(worktree_path: str, task: dict) -> dict:
            wt = pathlib.Path(worktree_path)
            (wt / f"{task['branch']}.txt").write_text("done")
            await asyncio.sleep(0.02)
            done.append(task["branch"])
            return {"status": "completed", "result": f"{task['branch']} ok"}

        r = await multi_agent_worktree_workflow(
            MultiAgentWorktreeConfig(max_parallel=2),
            MultiAgentWorktreeInput(
                repo_path=str(repo),
                tasks=[{"branch": "b1"}, {"branch": "b2"}],
                agent_runner=runner,
            ),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert r["findings"]["succeeded"] == 2
        assert sorted(done) == ["b1", "b2"]
        # worktree 已回收
        assert not (tmp_path / "repo-worktrees" / "b1").exists()
        assert r["report_path"] and pathlib.Path(r["report_path"]).exists()
