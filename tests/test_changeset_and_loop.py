"""
apply_changeset + agentic_loop 测试套件
========================================

apply_changeset (omodul, ≥10 场景):
 1. full_content 写盘正常完成
 2. edit_blocks 模糊匹配成功
 3. unified_diff 应用成功
 4. 语法错误 → rolled_back（Python .py 文件）
 5. edit_block 找不到 search → rolled_back
 6. versionstore 快照 → restore 验证（undo 语义）
 7. fingerprint 稳定性（同 input 同 fingerprint）
 8. fingerprint 差异性（不同 input 不同 fingerprint）
 9. 多文件：第 2 个失败 → 全部回滚（含第 1 个已写盘）
10. sandbox 越界路径 → status=failed（不写盘）
11. on_step 回调触发验证
12. decision_trail 落盘验证

agentic_loop (oservice, ≥10 场景):
 1. 装配缺 required 注入点 → ManifestValidationError
 2. 正常完成：LLM end_turn，无工具调用
 3. 工具调用：LLM 请求工具 → 执行 → 继续
 4. hook block：PreToolUse 返回 block → 工具跳过
 5. permission deny：plan 模式 write 工具被过滤
 6. budget_exceeded：cost 超限后停止
 7. max_iterations：超过上限后停止
 8. on_step 回调全程触发
 9. 未知工具名 → tool_not_found，循环继续
10. 上下文压缩：超阈值触发 _maybe_compact（截断策略）
11. hook 修改 task（UserPromptSubmit）
12. health() 状态正确反映引擎状态
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "oservice"))
sys.path.insert(0, str(Path(__file__).parent))

from omodul import (
    ChangesetConfig,
    ChangesetInput,
    Edit,
    EditBlock,
    VersionStore,
    apply_changeset,
    compute_fingerprint_for,
)
from oservi.agentic_loop import (
    AgenticLoop,
    ManifestValidationError,
    ToolSpec,
)

PASSED: list[str] = []
FAILED: list[str] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    sym = "✅ PASS" if ok else "❌ FAIL"
    print(f"  {sym}  {name}" + (f"  [{detail}]" if detail else ""))
    (PASSED if ok else FAILED).append(name)


def tmp_dir():
    return tempfile.TemporaryDirectory()


# ============================================================
# apply_changeset 测试
# ============================================================

def cs_config(**kw) -> ChangesetConfig:
    return ChangesetConfig(**kw)


def cs_input(edits, vstore=None, message="test") -> ChangesetInput:
    return ChangesetInput(edits=edits, versionstore=vstore, message=message)


def test_cs_01_full_content():
    """full_content 写盘正常完成。"""
    with tmp_dir() as d:
        p = Path(d) / "a.txt"
        edits = [Edit(path=str(p), full_content="hello world", validate_syntax=False)]
        result = apply_changeset(cs_config(), cs_input(edits), Path(d) / "out")
        ok = (
            result["status"] == "completed"
            and str(p) in result["applied"]
            and p.read_text() == "hello world"
        )
        report("cs_01_full_content", ok, f"status={result['status']}")


def test_cs_02_edit_blocks_fuzzy():
    """edit_blocks 模糊匹配成功（忽略行首尾空白）。"""
    with tmp_dir() as d:
        p = Path(d) / "b.txt"
        p.write_text("  hello\n  world\n")
        edits = [Edit(
            path=str(p),
            blocks=[EditBlock(search="hello", replace="goodbye")],
            validate_syntax=False,
        )]
        result = apply_changeset(cs_config(), cs_input(edits), Path(d) / "out")
        ok = result["status"] == "completed" and "goodbye" in p.read_text()
        report("cs_02_edit_blocks_fuzzy", ok, f"status={result['status']}")


def test_cs_03_unified_diff():
    """unified_diff 应用成功。"""
    with tmp_dir() as d:
        p = Path(d) / "c.txt"
        p.write_text("line1\nline2\nline3\n")
        diff = (
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-line2\n"
            "+LINE2\n"
            " line3\n"
        )
        edits = [Edit(path=str(p), unified_diff=diff, validate_syntax=False)]
        result = apply_changeset(cs_config(), cs_input(edits), Path(d) / "out")
        ok = result["status"] == "completed" and "LINE2" in p.read_text()
        report("cs_03_unified_diff", ok, f"status={result['status']} content={p.read_text()!r}")


def test_cs_04_syntax_error_rollback():
    """Python 文件语法错误 → rolled_back，原文件不变。"""
    with tmp_dir() as d:
        p = Path(d) / "d.py"
        original = "x = 1\n"
        p.write_text(original)
        vstore = VersionStore()
        edits = [Edit(
            path=str(p),
            full_content="def broken(\n",   # 语法错误
            validate_syntax=True,
        )]
        result = apply_changeset(
            cs_config(syntax_check_enabled=True),
            cs_input(edits, vstore),
            Path(d) / "out",
        )
        ok = (
            result["status"] == "rolled_back"
            and p.read_text() == original
            and result["applied"] == []
        )
        report("cs_04_syntax_error_rollback", ok,
               f"status={result['status']} file={p.read_text()!r}")


def test_cs_05_block_not_found_rollback():
    """edit_block search 找不到 → rolled_back。"""
    with tmp_dir() as d:
        p = Path(d) / "e.txt"
        p.write_text("alpha beta\n")
        vstore = VersionStore()
        edits = [Edit(
            path=str(p),
            blocks=[EditBlock(search="NONEXISTENT", replace="x")],
            validate_syntax=False,
        )]
        result = apply_changeset(cs_config(), cs_input(edits, vstore), Path(d) / "out")
        ok = result["status"] == "rolled_back" and p.read_text() == "alpha beta\n"
        report("cs_05_block_not_found_rollback", ok, f"status={result['status']}")


def test_cs_06_versionstore_undo():
    """versionstore 快照 + restore 实现 undo 语义。"""
    with tmp_dir() as d:
        p = Path(d) / "f.txt"
        p.write_text("original\n")
        vstore = VersionStore()
        edits = [Edit(path=str(p), full_content="modified\n", validate_syntax=False)]
        result = apply_changeset(cs_config(), cs_input(edits, vstore), Path(d) / "out")
        assert result["status"] == "completed"
        assert p.read_text() == "modified\n"

        # undo：restore 快照
        rev = result["snapshot_rev"]
        vstore.restore(rev)
        ok = p.read_text() == "original\n"
        report("cs_06_versionstore_undo", ok,
               f"after_undo={p.read_text()!r}")


def test_cs_07_fingerprint_stable():
    """同 input → 同 fingerprint（稳定性）。"""
    config = cs_config()
    edits = [Edit(path="/tmp/x.py", full_content="x=1")]
    inp = cs_input(edits)
    fp1 = compute_fingerprint_for(config, inp)
    fp2 = compute_fingerprint_for(config, inp)
    ok = fp1 == fp2 and len(fp1) == 24
    report("cs_07_fingerprint_stable", ok, f"fp={fp1}")


def test_cs_08_fingerprint_distinct():
    """不同 input → 不同 fingerprint。"""
    config = cs_config()
    inp_a = cs_input([Edit(path="/tmp/a.py", full_content="x=1")])
    inp_b = cs_input([Edit(path="/tmp/a.py", full_content="x=2")])
    fp_a = compute_fingerprint_for(config, inp_a)
    fp_b = compute_fingerprint_for(config, inp_b)
    ok = fp_a != fp_b
    report("cs_08_fingerprint_distinct", ok, f"fp_a={fp_a} fp_b={fp_b}")


def test_cs_09_multi_file_second_fails_rollback():
    """多文件：第 2 个语法错误 → 全部回滚（含第 1 个已写盘的）。"""
    with tmp_dir() as d:
        p1 = Path(d) / "g1.py"
        p2 = Path(d) / "g2.py"
        p1.write_text("a = 1\n")
        p2.write_text("b = 2\n")
        vstore = VersionStore()
        edits = [
            Edit(path=str(p1), full_content="a = 99\n", validate_syntax=True),
            Edit(path=str(p2), full_content="def broken(\n", validate_syntax=True),
        ]
        result = apply_changeset(
            cs_config(syntax_check_enabled=True),
            cs_input(edits, vstore),
            Path(d) / "out",
        )
        # 第 1 个写盘后第 2 个失败 → 全部回滚
        ok = (
            result["status"] == "rolled_back"
            and p1.read_text() == "a = 1\n"   # 已回滚
            and p2.read_text() == "b = 2\n"   # 未被改动
        )
        report("cs_09_multi_file_second_fails_rollback", ok,
               f"p1={p1.read_text()!r} p2={p2.read_text()!r}")


def test_cs_10_sandbox_violation():
    """sandbox_root 下路径越界 → status=failed，不写盘。"""
    with tmp_dir() as d:
        sandbox = Path(d) / "sandbox"
        sandbox.mkdir()
        outside = Path(d) / "secret.txt"
        edits = [Edit(path=str(outside), full_content="hacked", validate_syntax=False)]
        result = apply_changeset(
            cs_config(sandbox_root=str(sandbox)),
            cs_input(edits),
            Path(d) / "out",
        )
        ok = result["status"] == "failed" and not outside.exists()
        report("cs_10_sandbox_violation", ok, f"status={result['status']}")


def test_cs_11_on_step_callbacks():
    """on_step 回调在 changeset_start 和 file_written 时触发。"""
    with tmp_dir() as d:
        p = Path(d) / "h.txt"
        events = []
        edits = [Edit(path=str(p), full_content="hi", validate_syntax=False)]
        apply_changeset(
            cs_config(),
            cs_input(edits),
            Path(d) / "out",
            on_step=lambda e: events.append(e["event"]),
        )
        ok = "changeset_start" in events and "file_written" in events
        report("cs_11_on_step_callbacks", ok, f"events={events}")


def test_cs_12_decision_trail_written():
    """decision_trail JSON 落盘，步骤数 > 0。"""
    with tmp_dir() as d:
        p = Path(d) / "i.txt"
        edits = [Edit(path=str(p), full_content="trail test", validate_syntax=False)]
        result = apply_changeset(cs_config(), cs_input(edits), Path(d) / "out")
        trail_path = Path(result["decision_trail"]["path"])
        ok = trail_path.exists() and result["decision_trail"]["steps"] > 0
        if ok:
            trail = json.loads(trail_path.read_text())
            ok = len(trail) > 0
        report("cs_12_decision_trail_written", ok,
               f"steps={result['decision_trail']['steps']}")


# ============================================================
# agentic_loop 测试
# ============================================================

def _text_response(text="done", in_tok=100, out_tok=50):
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    }


def _tool_response(tool_name, tool_input, in_tok=100, out_tok=30):
    return {
        "content": [
            {"type": "tool_use", "id": "t1", "name": tool_name, "input": tool_input}
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    }


def make_caller(responses):
    idx = 0

    async def caller(*, messages, tools=None, max_tokens=8192,
                     thinking_budget=None, system=None):
        nonlocal idx
        r = responses[min(idx, len(responses) - 1)]
        idx += 1
        return r

    return caller


def make_echo_tool(name="echo_tool", readonly=False):
    async def fn(inp):
        return {"echoed": inp.get("msg", "ok")}

    return ToolSpec(
        name=name,
        description="Echo input",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
        callable=fn,
        readonly=readonly,
    )


def make_loop(mode="build", max_iter=10, budget=10.0) -> AgenticLoop:
    return AgenticLoop(
        max_iterations=max_iter,
        budget_usd=budget,
        mode=mode,
        output_dir=Path("/tmp/hicode_test"),
    )


async def test_al_01_missing_required_injection():
    """装配缺 required 注入点 → ManifestValidationError。"""
    loop = make_loop()
    try:
        loop.assemble()  # 缺 llm_caller 和 tools
        report("al_01_missing_required_injection", False, "should have raised")
    except ManifestValidationError as e:
        report("al_01_missing_required_injection", True, str(e)[:60])


async def test_al_02_normal_completion():
    """正常完成：LLM end_turn，无工具调用。"""
    loop = make_loop()
    loop.assemble(
        llm_caller=make_caller([_text_response("all done")]),
        tools=[make_echo_tool()],
    )
    loop.run()
    result = await loop.session("do something")
    ok = result["status"] == "completed" and "all done" in result["result"]
    report("al_02_normal_completion", ok,
           f"status={result['status']} result={result['result']!r}")


async def test_al_03_tool_call_execution():
    """LLM 请求工具 → 执行 → end_turn。"""
    events = []
    loop = make_loop()
    loop.assemble(
        llm_caller=make_caller([
            _tool_response("echo_tool", {"msg": "hello"}),
            _text_response("tool done"),
        ]),
        tools=[make_echo_tool()],
    )
    loop.run()
    result = await loop.session("use tool", on_step=lambda e: events.append(e["event"]))
    tool_called = any(e == "tool_call" for e in events)
    ok = result["status"] == "completed" and tool_called
    report("al_03_tool_call_execution", ok,
           f"status={result['status']} tool_called={tool_called}")


async def test_al_04_hook_block():
    """PreToolUse hook block → 工具跳过，循环继续。"""
    blocked = []

    async def hook_dispatch(event, payload):
        if event == "PreToolUse":
            blocked.append(payload.get("tool"))
            return {"decision": "block", "modified_payload": payload}
        return {"decision": "allow", "modified_payload": payload}

    loop = make_loop()
    loop.assemble(
        llm_caller=make_caller([
            _tool_response("echo_tool", {"msg": "x"}),
            _text_response("done after block"),
        ]),
        tools=[make_echo_tool()],
        hook_dispatch=hook_dispatch,
    )
    loop.run()
    result = await loop.session("test hook")
    ok = result["status"] == "completed" and "echo_tool" in blocked
    report("al_04_hook_block", ok, f"blocked={blocked}")


async def test_al_05_plan_mode_filters_write_tools():
    """plan 模式：只读工具可用，非只读工具被过滤出 schema。"""
    schema_sent = []
    read_tool = make_echo_tool("read_tool", readonly=True)
    write_tool = make_echo_tool("write_tool", readonly=False)

    async def capturing_caller(*, messages, tools=None, **kw):
        schema_sent.append(tools or [])
        return _text_response("plan ready")

    loop = make_loop(mode="plan")
    loop.assemble(
        llm_caller=capturing_caller,
        tools=[read_tool, write_tool],
    )
    loop.run()
    await loop.session("plan something")
    sent_names = {t["name"] for t in (schema_sent[0] if schema_sent else [])}
    ok = "read_tool" in sent_names and "write_tool" not in sent_names
    report("al_05_plan_mode_filters_write_tools", ok, f"sent_names={sent_names}")


async def test_al_06_budget_exceeded():
    """cost 超限 → status=budget_exceeded。"""
    loop = AgenticLoop(
        max_iterations=10,
        budget_usd=0.0001,  # 极小 budget
        output_dir=Path("/tmp/hicode_test"),
    )
    loop.assemble(
        llm_caller=make_caller([_text_response(in_tok=10000, out_tok=5000)]),
        tools=[make_echo_tool()],
    )
    loop.run()
    result = await loop.session("expensive task")
    ok = result["status"] == "budget_exceeded"
    report("al_06_budget_exceeded", ok,
           f"status={result['status']} cost={result['cost_usd']}")


async def test_al_07_max_iterations():
    """max_iterations=2 → 循环 ≤2 次 LLM 调用。"""
    calls = []

    async def counting_caller(*, messages, tools=None, **kw):
        calls.append(1)
        # 永远返回 tool_use，迫使引擎靠 max_iterations 停
        return _tool_response("echo_tool", {"msg": "loop"})

    loop = AgenticLoop(max_iterations=2, output_dir=Path("/tmp/hicode_test"))
    loop.assemble(
        llm_caller=counting_caller,
        tools=[make_echo_tool()],
    )
    loop.run()
    result = await loop.session("infinite loop test")
    ok = len(calls) <= 2
    report("al_07_max_iterations", ok,
           f"llm_calls={len(calls)} status={result['status']}")


async def test_al_08_on_step_events():
    """on_step 回调：session_start / iteration_done / session_done 全部触发。"""
    events = []
    loop = make_loop()
    loop.assemble(
        llm_caller=make_caller([_text_response()]),
        tools=[make_echo_tool()],
    )
    loop.run()
    await loop.session("event test", on_step=lambda e: events.append(e["event"]))
    ok = (
        "session_start" in events
        and "session_done" in events
    )
    report("al_08_on_step_events", ok, f"events={events}")


async def test_al_09_unknown_tool_continues():
    """LLM 请求不存在工具 → tool_not_found，引擎继续不崩溃。"""
    loop = make_loop()
    loop.assemble(
        llm_caller=make_caller([
            _tool_response("nonexistent_tool", {}),
            _text_response("recovered"),
        ]),
        tools=[make_echo_tool()],
    )
    loop.run()
    result = await loop.session("unknown tool test")
    ok = result["status"] == "completed" and "recovered" in result["result"]
    report("al_09_unknown_tool_continues", ok, f"status={result['status']}")


async def test_al_10_context_compaction():
    """上下文超阈值 → _maybe_compact 截断（无 compactor 注入时）。"""
    # 把 context_limit 设很小，迫使压缩
    loop = AgenticLoop(
        context_limit_tokens=10,
        compaction_threshold=0.1,
        max_iterations=3,
        output_dir=Path("/tmp/hicode_test"),
    )
    compacted = []
    orig_compact = loop._maybe_compact

    async def tracking_compact(messages, state, on_step):
        compacted.append(len(messages))
        return await orig_compact(messages, state, on_step)

    loop._maybe_compact = tracking_compact
    loop.assemble(
        llm_caller=make_caller([
            _tool_response("echo_tool", {"msg": "a"}),
            _text_response("done"),
        ]),
        tools=[make_echo_tool()],
    )
    loop.run()
    await loop.session("compaction test")
    ok = len(compacted) > 0
    report("al_10_context_compaction", ok, f"compaction_triggered={len(compacted)} times")


async def test_al_11_hook_modifies_task():
    """UserPromptSubmit hook 修改 task → LLM 收到修改后的任务。"""
    received_tasks = []

    async def hook_dispatch(event, payload):
        if event == "UserPromptSubmit":
            return {
                "decision": "allow",
                "modified_payload": {"task": "MODIFIED: " + payload.get("task", "")},
            }
        return {"decision": "allow", "modified_payload": payload}

    async def capturing_caller(*, messages, tools=None, **kw):
        received_tasks.append(messages[0]["content"] if messages else "")
        return _text_response("done")

    loop = make_loop()
    loop.assemble(
        llm_caller=capturing_caller,
        tools=[make_echo_tool()],
        hook_dispatch=hook_dispatch,
    )
    loop.run()
    await loop.session("original task")
    ok = received_tasks and "MODIFIED:" in received_tasks[0]
    report("al_11_hook_modifies_task", ok, f"received={received_tasks[0]!r}")


async def test_al_12_health_status():
    """health() 在 run() 前后状态正确。"""
    loop = make_loop()
    loop.assemble(
        llm_caller=make_caller([_text_response()]),
        tools=[make_echo_tool()],
    )
    h_before = loop.health()
    loop.run()
    h_after = loop.health()
    ok = h_before["status"] == "stopped" and h_after["status"] == "healthy"
    report("al_12_health_status", ok,
           f"before={h_before['status']} after={h_after['status']}")


# ============================================================
# 主运行器
# ============================================================

async def main():
    print("\n" + "=" * 65)
    print("  apply_changeset + agentic_loop 测试套件")
    print("=" * 65 + "\n")

    print("── apply_changeset (omodul) ──────────────────────────────────")
    sync_tests = [
        test_cs_01_full_content,
        test_cs_02_edit_blocks_fuzzy,
        test_cs_03_unified_diff,
        test_cs_04_syntax_error_rollback,
        test_cs_05_block_not_found_rollback,
        test_cs_06_versionstore_undo,
        test_cs_07_fingerprint_stable,
        test_cs_08_fingerprint_distinct,
        test_cs_09_multi_file_second_fails_rollback,
        test_cs_10_sandbox_violation,
        test_cs_11_on_step_callbacks,
        test_cs_12_decision_trail_written,
    ]
    for t in sync_tests:
        try:
            t()
        except Exception as e:
            report(t.__name__, False, f"UNHANDLED: {e}")

    print()
    print("── agentic_loop (oservice) ───────────────────────────────────")
    async_tests = [
        test_al_01_missing_required_injection,
        test_al_02_normal_completion,
        test_al_03_tool_call_execution,
        test_al_04_hook_block,
        test_al_05_plan_mode_filters_write_tools,
        test_al_06_budget_exceeded,
        test_al_07_max_iterations,
        test_al_08_on_step_events,
        test_al_09_unknown_tool_continues,
        test_al_10_context_compaction,
        test_al_11_hook_modifies_task,
        test_al_12_health_status,
    ]
    for t in async_tests:
        try:
            await t()
        except Exception as e:
            report(t.__name__, False, f"UNHANDLED: {e}")

    print()
    print("=" * 65)
    total = len(PASSED) + len(FAILED)
    print(f"  结果: {len(PASSED)}/{total} 通过")
    if FAILED:
        print(f"  失败: {', '.join(FAILED)}")
    print("=" * 65 + "\n")
    return len(FAILED) == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
