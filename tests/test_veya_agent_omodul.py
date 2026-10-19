"""Tests for Veya 顶级 Agent 增强元素 — omodul 层 (2 个).

Covers: hitl_approval_workflow, mcp_tool_export_workflow.
"""

from __future__ import annotations

import asyncio
import pathlib


class TestHitlApprovalWorkflow:
    async def test_wait_approved(self, tmp_path: pathlib.Path):
        from obase import HitlSignalBus

        from omodul import HitlApprovalConfig, HitlApprovalInput, hitl_approval_workflow

        bus = HitlSignalBus()
        rid = bus.request_approval(scope="checkout", summary="订单#1")

        async def human():
            await asyncio.sleep(0.03)
            bus.approve(rid, note="looks good")

        r = await asyncio.gather(
            hitl_approval_workflow(
                HitlApprovalConfig(timeout=5.0),
                HitlApprovalInput(action="wait", request_id=rid, bus=bus),
                tmp_path,
            ),
            human(),
        )
        r = r[0]
        assert r["status"] == "completed"
        assert r["decision"]["outcome"] == "approved"
        assert r["decision"]["note"] == "looks good"
        assert r["fingerprint"]
        assert r["decision_trail"]["steps"] >= 1
        # trail 落盘
        assert pathlib.Path(r["decision_trail"]["path"]).exists()

    async def test_approve_reject_actions(self, tmp_path: pathlib.Path):
        from obase import HitlSignalBus

        from omodul import HitlApprovalConfig, HitlApprovalInput, hitl_approval_workflow

        bus = HitlSignalBus()
        rid = bus.request_approval(scope="deploy", summary="v2")
        r = await hitl_approval_workflow(
            HitlApprovalConfig(),
            HitlApprovalInput(action="approve", request_id=rid, note="go", bus=bus),
            tmp_path,
        )
        assert r["status"] == "completed" and r["decided"] is True
        assert r["decision"]["outcome"] == "approved"

        rid2 = bus.request_approval(scope="deploy", summary="v3")
        r = await hitl_approval_workflow(
            HitlApprovalConfig(),
            HitlApprovalInput(action="reject", request_id=rid2, note="no", bus=bus),
            tmp_path,
        )
        assert r["status"] == "completed" and r["decision"]["outcome"] == "rejected"

    async def test_status_action(self, tmp_path: pathlib.Path):
        from obase import HitlSignalBus

        from omodul import HitlApprovalConfig, HitlApprovalInput, hitl_approval_workflow

        bus = HitlSignalBus()
        rid = bus.request_approval(scope="s", summary="x")
        r = await hitl_approval_workflow(
            HitlApprovalConfig(),
            HitlApprovalInput(action="status", request_id=rid, bus=bus),
            tmp_path,
        )
        assert r["status"] == "completed" and r["decision"]["outcome"] == "pending"

    async def test_no_bus_fails_gracefully(self, tmp_path: pathlib.Path):
        from omodul import HitlApprovalConfig, HitlApprovalInput, hitl_approval_workflow

        r = await hitl_approval_workflow(
            HitlApprovalConfig(),
            HitlApprovalInput(action="request", scope="x", summary="y"),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "bus" in r["error"]["message"]

    async def test_unknown_action_fails(self, tmp_path: pathlib.Path):
        from obase import HitlSignalBus

        from omodul import HitlApprovalConfig, HitlApprovalInput, hitl_approval_workflow

        bus = HitlSignalBus()
        r = await hitl_approval_workflow(
            HitlApprovalConfig(),
            HitlApprovalInput(action="status", request_id="nope", bus=bus),  # type: ignore[arg-type]
            tmp_path,
        )
        assert r["status"] == "failed"

    async def test_fingerprint_deterministic(self, tmp_path: pathlib.Path):
        from omodul import HitlApprovalConfig, HitlApprovalInput
        from omodul.hitl_approval_workflow import compute_fingerprint_for

        inp = HitlApprovalInput(action="request", scope="s", summary="x")
        assert compute_fingerprint_for(HitlApprovalConfig(), inp) == compute_fingerprint_for(
            HitlApprovalConfig(), inp
        )


class TestMcpToolExportWorkflow:
    async def test_export_tools(self, tmp_path: pathlib.Path):
        from omodul import McpToolExportConfig, McpToolExportInput, mcp_tool_export_workflow

        async def hello(args):
            return {"msg": f"hi {args.get('name')}"}

        r = await mcp_tool_export_workflow(
            McpToolExportConfig(server_name="veya", server_version="1.0.0"),
            McpToolExportInput(
                tools=[
                    {
                        "name": "veya.hello",
                        "description": "Say hi",
                        "input_schema": {
                            "type": "object",
                            "properties": {"user_name": {"type": "string"}},
                            "required": ["user_name"],
                        },
                        "handler": hello,
                    }
                ]
            ),
            tmp_path,
        )
        assert r["status"] == "completed", r
        assert r["findings"]["registered"] == 1
        assert r["findings"]["tools"][0]["name"] == "veya.hello"
        # Schema camelCase 化
        assert "userName" in r["findings"]["tools"][0]["input_schema"]["properties"]
        assert r["decision_trail"]["steps"] >= 1

    async def test_empty_tools_fails(self, tmp_path: pathlib.Path):
        from omodul import McpToolExportConfig, McpToolExportInput, mcp_tool_export_workflow

        r = await mcp_tool_export_workflow(
            McpToolExportConfig(), McpToolExportInput(tools=[]), tmp_path
        )
        assert r["status"] == "failed"
        assert "tools" in r["error"]["message"]

    async def test_missing_schema_fails(self, tmp_path: pathlib.Path):
        from omodul import McpToolExportConfig, McpToolExportInput, mcp_tool_export_workflow

        async def h(args):
            return {}

        r = await mcp_tool_export_workflow(
            McpToolExportConfig(),
            McpToolExportInput(tools=[{"name": "t", "description": "d", "handler": h}]),
            tmp_path,
        )
        assert r["status"] == "failed"
        assert "input_schema" in r["error"]["message"]

    async def test_injected_server_reused(self, tmp_path: pathlib.Path):
        from obase.mcp_server import MCPServer

        from omodul import McpToolExportConfig, McpToolExportInput, mcp_tool_export_workflow

        server = MCPServer(name="pre", version="9")
        async def h(args):
            return {}

        r = await mcp_tool_export_workflow(
            McpToolExportConfig(),
            McpToolExportInput(
                tools=[{"name": "pre.t", "description": "t",
                        "input_schema": {"type": "object", "properties": {}},
                        "handler": h}],
                server=server,
            ),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert server.has_tool("pre.t")

    async def test_llm_generated_description(self, tmp_path: pathlib.Path):
        from omodul import McpToolExportConfig, McpToolExportInput, mcp_tool_export_workflow

        class FakeCaller:
            async def __call__(self, *, messages, tools=None, max_tokens=4096, stop_sequences=None):
                return {
                    "content": [{"type": "text", "text": "Fetches weather data"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "stop_reason": "end_turn",
                }

        async def h(args):
            return {}

        r = await mcp_tool_export_workflow(
            McpToolExportConfig(),
            McpToolExportInput(
                tools=[{"name": "v.weather", "description": "",
                        "input_schema": {"type": "object", "properties": {}},
                        "handler": h}],
                caller=FakeCaller(),
            ),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert r["findings"]["tools"][0]["description"] == "Fetches weather data"
        assert r["cost_usd"] > 0
