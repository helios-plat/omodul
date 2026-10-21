"""3O 范式内化 — omodul 事务测试 (Graft 构建 / OpenRSI 演化 / TTS 缩放)."""

from __future__ import annotations

from obase import MemoryBank, MemoryItem
from obase.sandbox_runner import SandboxResult

from omodul import (
    ContextBuilderConfig,
    ContextInput,
    EvolutionConfig,
    EvolutionInput,
    TTSConfig,
    TTSInput,
    build_context_workflow,
    evaluate_task_complexity,
    run_evolution_workflow,
    tts_scale_workflow,
)


class _FakeLLM:
    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls = 0

    async def __call__(self, *, messages, tools=None, max_tokens=4096):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return {"content": reply}


# ---------------------------------------------------------------------------
# Graft: context_builder
# ---------------------------------------------------------------------------


async def test_context_builder_incremental_zero_cost(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "auth.py").write_text("import os\ndef auth(): pass\n", encoding="utf-8")

    llm = _FakeLLM(["Handles auth logic."])
    inp = ContextInput(
        anon_task_ref="anon-1", workspace_dir=str(workspace), files=["auth.py"]
    )
    r1 = await build_context_workflow(ContextBuilderConfig(), inp, tmp_path / "out", llm=llm)
    assert r1["status"] == "completed"
    assert r1["findings"]["built"] == ["auth"]
    node_md = workspace / ".veya" / "graph" / "auth.md"
    assert node_md.exists()
    assert "Handles auth logic." in node_md.read_text(encoding="utf-8")

    # 第二次: Hash 未变 → 0 成本跳过 (不再调用 LLM)
    r2 = await build_context_workflow(ContextBuilderConfig(), inp, tmp_path / "out2", llm=llm)
    assert r2["status"] == "completed"
    assert r2["findings"]["built"] == []
    assert r2["findings"]["skipped"] == 1
    assert llm.calls == 1  # 未触发新摘要


async def test_context_builder_detects_change(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    f = workspace / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    llm = _FakeLLM(["s1"])
    inp = ContextInput(anon_task_ref="anon-1", workspace_dir=str(workspace), files=["a.py"])
    await build_context_workflow(ContextBuilderConfig(), inp, tmp_path / "o1", llm=llm)
    f.write_text("x = 2\n", encoding="utf-8")  # 内容变更
    r = await build_context_workflow(ContextBuilderConfig(), inp, tmp_path / "o2", llm=llm)
    assert r["findings"]["built"] == ["a"]


# ---------------------------------------------------------------------------
# OpenRSI: evolution_engine
# ---------------------------------------------------------------------------


class _PassingSandbox:
    """假沙盒: 固定返回测试全过。"""

    async def __call__(self, code_dir: str, command: str, timeout_sec: int) -> SandboxResult:
        return SandboxResult(
            exit_code=0,
            stdout="collected 2 items\n..\n=== 2 passed in 0.01s ===\n",
            execution_time_ms=10,
        )


async def test_evolution_engine_stops_at_100pct(tmp_path):
    llm = _FakeLLM(["```python\nprint('v1')\n```"] * 10)
    r = await run_evolution_workflow(
        EvolutionConfig(branch_count=3, max_depth=5, max_sandbox_calls=20),
        EvolutionInput(anon_task_ref="anon-1", task_id="T-1", requirement="add two numbers"),
        tmp_path / "evo",
        llm=llm,
        sandbox=_PassingSandbox(),
    )
    assert r["status"] == "completed"
    assert r["findings"]["best_score"] == 1.0
    assert r["findings"]["branches_explored"] == 3  # 首轮即 100% → 不再演化
    assert r["findings"]["sandbox_calls"] == 3
    assert (tmp_path / "evo" / "decision_trail.json").exists()


class _FailingSandbox:
    """假沙盒: 固定全败 + 错误信息 → 触发 Evo_Debug。"""

    async def __call__(self, code_dir: str, command: str, timeout_sec: int) -> SandboxResult:
        return SandboxResult(
            exit_code=1,
            stdout="collected 1 item\nF\n=== 0 passed, 1 failed in 0.01s ===\n",
            stderr="AssertionError: boom at test.py:3",
            execution_time_ms=10,
        )


async def test_evolution_engine_all_fail_triggers_debug(tmp_path):
    replies = ["```python\nv1\n```", "```python\nfixed_v2\n```", "```python\nfixed_v3\n```"]
    llm = _FakeLLM(replies)
    r = await run_evolution_workflow(
        EvolutionConfig(branch_count=2, max_depth=2, max_sandbox_calls=10),
        EvolutionInput(anon_task_ref="anon-1", task_id="T-2", requirement="fix bug"),
        tmp_path / "evo2",
        llm=llm,
        sandbox=_FailingSandbox(),
    )
    assert r["status"] == "completed"
    assert r["findings"]["branches_explored"] >= 3  # 2 初始 + Debug 新分支
    trails = r["findings"]["trajectories"]
    assert any(t["operator_used"] == "Evo_Debug" for t in trails)


# ---------------------------------------------------------------------------
# ReasoningBank: tts_manager
# ---------------------------------------------------------------------------


def test_evaluate_task_complexity():
    assert (
        evaluate_task_complexity(
            "fix bug", files_touched=["a.py"], error_prone_domains=["deadlock"]
        )
        == "NORMAL_TASK"
    )
    assert (
        evaluate_task_complexity(
            "fix deadlock", files_touched=["a.py"], error_prone_domains=["deadlock", "mysql"]
        )
        == "HARD_TASK"
    )
    assert (
        evaluate_task_complexity(
            "refactor", files_touched=["a.py", "b.py"], error_prone_domains=[]
        )
        == "HARD_TASK"
    )


async def test_tts_scale_workflow_hard_task(tmp_path):
    bank = MemoryBank(str(tmp_path / "mem.db"))
    await bank.add(
        MemoryItem(
            memory_id="MEM-1",
            context_description="mysql deadlock fix",
            failed_attempt={"action": "x", "reason": "hold lock too long"},
            successful_strategy={"action": "y", "reason": "shorten lock duration"},
        )
    )
    r = await tts_scale_workflow(
        TTSConfig(),
        TTSInput(
            anon_task_ref="anon-1",
            task_description="fix mysql deadlock in checkout",
            files_touched=["a.py", "b.py"],
            error_prone_domains=["mysql"],
        ),
        tmp_path / "tts",
        memory=bank,
    )
    assert r["status"] == "completed"
    assert r["findings"]["difficulty"] == "HARD_TASK"
    assert r["findings"]["branch_count"] == 6  # N=3 → N=6 缩放
    assert r["findings"]["scaled_up"] is True
    assert len(r["findings"]["constraints"]) == 1  # 经验注入为硬约束


async def test_tts_scale_workflow_normal_task(tmp_path):
    bank = MemoryBank(str(tmp_path / "mem2.db"))
    r = await tts_scale_workflow(
        TTSConfig(),
        TTSInput(
            anon_task_ref="anon-2",
            task_description="rename variable",
            files_touched=["a.py"],
        ),
        tmp_path / "tts2",
        memory=bank,
    )
    assert r["findings"]["difficulty"] == "NORMAL_TASK"
    assert r["findings"]["branch_count"] == 3
    assert r["findings"]["constraints"] == []
