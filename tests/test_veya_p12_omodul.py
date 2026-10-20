"""Tests for Veya P1/P2 顶级 Agent 功能复现 — omodul 层 (5 个).

Covers: nl_agent_synthesis_workflow, swarm_collaborative_workflow,
durable_lease_task_workflow, realtime_voice_agent_workflow,
content_media_pipeline_workflow.
"""

from __future__ import annotations

import pathlib
import struct


class FakeLLM:
    async def __call__(self, *, messages, tools=None, max_tokens=4096, stop_sequences=None):
        return {"content": [{"type": "text", "text": "你是媒体助手"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn"}


class FakeTranscriber:
    async def transcribe(self, audio, *, language=None):
        return {"text": "你好"}


class FakeSynthesizer:
    async def synthesize_stream(self, text, *, voice=None):
        for _ in range(2):
            yield b"\x00" * 320


class FakePublisher:
    async def publish(self, channel, *, content, media_paths):
        return {"post_id": "p-9"}


class TestNlAgentSynthesisWorkflow:
    async def test_synthesis(self, tmp_path: pathlib.Path):
        from omodul import (
            NlAgentSynthesisConfig,
            NlAgentSynthesisInput,
            nl_agent_synthesis_workflow,
        )

        r = await nl_agent_synthesis_workflow(
            NlAgentSynthesisConfig(),
            NlAgentSynthesisInput(
                goal="做数据分析",
                capabilities=["chart"],
                caller=FakeLLM(),
                tools=[{"name": "t1", "description": "d",
                        "input_schema": {"type": "object",
                                         "properties": {"user_id": {"type": "string"}}}}],
            ),
            tmp_path,
        )
        assert r["status"] == "completed", r
        assert "你是媒体助手" in r["findings"]["prompt"]
        assert r["findings"]["tools"][0]["input_schema"]["properties"]["userId"]  # camelCase
        assert r["cost_usd"] > 0
        assert r["report_path"] and pathlib.Path(r["report_path"]).exists()
        assert r["fingerprint"]

    async def test_no_caller_fails(self, tmp_path: pathlib.Path):
        from omodul import (
            NlAgentSynthesisConfig,
            NlAgentSynthesisInput,
            nl_agent_synthesis_workflow,
        )

        r = await nl_agent_synthesis_workflow(
            NlAgentSynthesisConfig(),
            NlAgentSynthesisInput(goal="x"),
            tmp_path,
        )
        assert r["status"] == "failed" and "caller" in r["error"]["message"]


class TestSwarmCollaborativeWorkflow:
    async def test_dispatch(self, tmp_path: pathlib.Path):
        from omodul import (
            SwarmCollaborativeConfig,
            SwarmCollaborativeInput,
            swarm_collaborative_workflow,
        )

        async def w1(task, payload):
            return f"w1:{task}"

        async def w2(task, payload):
            return f"w2:{task}"

        r = await swarm_collaborative_workflow(
            SwarmCollaborativeConfig(),
            SwarmCollaborativeInput(
                mission="并行调研",
                tasks=[{"id": "a"}, {"id": "b"}],
                workers={"w1": w1, "w2": w2},
            ),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert r["findings"]["completed"] == 2
        assert r["findings"]["assignments"] == {"a": "w1", "b": "w2"}
        assert r["report_path"] and pathlib.Path(r["report_path"]).exists()

    async def test_partial_failure(self, tmp_path: pathlib.Path):
        from omodul import (
            SwarmCollaborativeConfig,
            SwarmCollaborativeInput,
            swarm_collaborative_workflow,
        )

        async def good(task, payload):
            return "ok"

        async def bad(task, payload):
            raise RuntimeError("boom")

        r = await swarm_collaborative_workflow(
            SwarmCollaborativeConfig(),
            SwarmCollaborativeInput(
                mission="m", tasks=[{"id": "a"}, {"id": "b"}],
                workers={"good": good, "bad": bad},
            ),
            tmp_path,
        )
        assert r["status"] == "completed"  # 部分失败仍 completed（结果含 errors）
        assert r["findings"]["failed"] == 1

    async def test_no_workers_fails(self, tmp_path: pathlib.Path):
        from omodul import (
            SwarmCollaborativeConfig,
            SwarmCollaborativeInput,
            swarm_collaborative_workflow,
        )

        r = await swarm_collaborative_workflow(
            SwarmCollaborativeConfig(),
            SwarmCollaborativeInput(mission="m", tasks=[{"id": "a"}]),
            tmp_path,
        )
        assert r["status"] == "failed" and "workers" in r["error"]["message"]


class TestDurableLeaseTaskWorkflow:
    async def test_enqueue_process(self, tmp_path: pathlib.Path):
        from obase import DurableLeaseQueue

        from omodul import (
            DurableLeaseTaskConfig,
            DurableLeaseTaskInput,
            durable_lease_task_workflow,
        )

        q = DurableLeaseQueue(tmp_path / "q.db")
        r = await durable_lease_task_workflow(
            DurableLeaseTaskConfig(),
            DurableLeaseTaskInput(action="enqueue", queue=q, task_payload={"x": 1}),
            tmp_path,
        )
        assert r["status"] == "completed" and r["item_id"]

        async def executor(payload, item_id):
            return "done"

        r = await durable_lease_task_workflow(
            DurableLeaseTaskConfig(),
            DurableLeaseTaskInput(action="process", queue=q, executor=executor),
            tmp_path,
        )
        assert r["status"] == "completed"
        assert r["leased"] and r["result"] == "done"
        assert q.pending_count() == 0

    async def test_failure_releases_back(self, tmp_path: pathlib.Path):
        from obase import DurableLeaseQueue

        from omodul import (
            DurableLeaseTaskConfig,
            DurableLeaseTaskInput,
            durable_lease_task_workflow,
        )

        q = DurableLeaseQueue(tmp_path / "q2.db")
        await durable_lease_task_workflow(
            DurableLeaseTaskConfig(),
            DurableLeaseTaskInput(action="enqueue", queue=q, task_payload={"y": 2}),
            tmp_path,
        )

        async def bad(payload, item_id):
            raise RuntimeError("boom")

        r = await durable_lease_task_workflow(
            DurableLeaseTaskConfig(),
            DurableLeaseTaskInput(action="process", queue=q, executor=bad),
            tmp_path,
        )
        assert r["status"] == "failed" and "boom" in r["error"]["message"]
        assert q.pending_count() == 1  # 失败归还

    async def test_no_queue_fails(self, tmp_path: pathlib.Path):
        from omodul import (
            DurableLeaseTaskConfig,
            DurableLeaseTaskInput,
            durable_lease_task_workflow,
        )

        r = await durable_lease_task_workflow(
            DurableLeaseTaskConfig(),
            DurableLeaseTaskInput(action="enqueue", task_payload={}),
            tmp_path,
        )
        assert r["status"] == "failed" and "queue" in r["error"]["message"]


class TestRealtimeVoiceAgentWorkflow:
    def _frames(self, with_speech: bool) -> list[bytes]:
        silence = b"\x00" * 320
        noise = struct.pack("<160h", *([30000] * 160))
        if with_speech:
            return [silence] * 2 + [noise] * 8
        return [silence] * 10

    async def test_full_turn(self, tmp_path: pathlib.Path):
        from obase import VADPipeline

        from omodul import (
            RealtimeVoiceAgentConfig,
            RealtimeVoiceAgentInput,
            realtime_voice_agent_workflow,
        )

        r = await realtime_voice_agent_workflow(
            RealtimeVoiceAgentConfig(),
            RealtimeVoiceAgentInput(
                frames=self._frames(True),
                vad=VADPipeline(mode="energy"),
                transcriber=FakeTranscriber(),
                caller=FakeLLM(),
                synthesizer=FakeSynthesizer(),
            ),
            tmp_path,
        )
        assert r["status"] == "completed", r
        assert r["heard"] is True
        assert r["transcript"] == "你好"
        assert r["reply"] == "你是媒体助手"
        assert r["tts"]["chunks"] == 2
        assert r["cost_usd"] > 0
        assert len(r["segments"]) >= 1

    async def test_silence_returns_early(self, tmp_path: pathlib.Path):
        from obase import VADPipeline

        from omodul import (
            RealtimeVoiceAgentConfig,
            RealtimeVoiceAgentInput,
            realtime_voice_agent_workflow,
        )

        r = await realtime_voice_agent_workflow(
            RealtimeVoiceAgentConfig(),
            RealtimeVoiceAgentInput(
                frames=self._frames(False),
                vad=VADPipeline(mode="energy"),
                transcriber=FakeTranscriber(),
                caller=FakeLLM(),
                synthesizer=FakeSynthesizer(),
            ),
            tmp_path,
        )
        assert r["status"] == "completed" and r["heard"] is False

    async def test_missing_injections_fails(self, tmp_path: pathlib.Path):
        from omodul import (
            RealtimeVoiceAgentConfig,
            RealtimeVoiceAgentInput,
            realtime_voice_agent_workflow,
        )

        r = await realtime_voice_agent_workflow(
            RealtimeVoiceAgentConfig(),
            RealtimeVoiceAgentInput(frames=[b"\x00" * 320]),
            tmp_path,
        )
        assert r["status"] == "failed"


class TestContentMediaPipelineWorkflow:
    async def test_pipeline(self, tmp_path: pathlib.Path):
        from omodul import (
            ContentMediaPipelineConfig,
            ContentMediaPipelineInput,
            content_media_pipeline_workflow,
        )

        img = tmp_path / "p.jpg"
        img.write_bytes(b"jpg")
        r = await content_media_pipeline_workflow(
            ContentMediaPipelineConfig(channel="x"),
            ContentMediaPipelineInput(
                content="新品发布", media_paths=[str(img)], publisher=FakePublisher()
            ),
            tmp_path,
        )
        assert r["status"] == "completed", r
        assert r["findings"]["post_id"] == "p-9"
        assert r["findings"]["media"][0]["kind"] == "image"
        assert r["findings"]["media_count"] == 1
        assert r["report_path"] and pathlib.Path(r["report_path"]).exists()

    async def test_no_publisher_fails(self, tmp_path: pathlib.Path):
        from omodul import (
            ContentMediaPipelineConfig,
            ContentMediaPipelineInput,
            content_media_pipeline_workflow,
        )

        r = await content_media_pipeline_workflow(
            ContentMediaPipelineConfig(),
            ContentMediaPipelineInput(content="x"),
            tmp_path,
        )
        assert r["status"] == "failed" and "publisher" in r["error"]["message"]

    async def test_missing_media_fails(self, tmp_path: pathlib.Path):
        from omodul import (
            ContentMediaPipelineConfig,
            ContentMediaPipelineInput,
            content_media_pipeline_workflow,
        )

        r = await content_media_pipeline_workflow(
            ContentMediaPipelineConfig(),
            ContentMediaPipelineInput(
                content="x", media_paths=["/nope.jpg"], publisher=FakePublisher()
            ),
            tmp_path,
        )
        assert r["status"] == "failed" and "not found" in r["error"]["message"]
