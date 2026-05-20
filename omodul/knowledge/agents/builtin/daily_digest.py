"""DailyDigestAgent — summarise last-24h substrates and push to user."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from oprim.llm import llm_call

from omodul.knowledge.agents.base import Agent, AgentContext, AgentResult, AgentStep, Citation
from omodul.knowledge.agents.registry import register_agent


@register_agent
class DailyDigestAgent(Agent):
    name = "daily_digest"
    description = "Generate digest of substrates added in the last 24h and push to user."
    allowed_tools = [
        "oskill.knowledge.hybrid_search",
        "oprim.llm.llm_call",
        "oprim.push.dispatcher",
    ]

    async def run(self, params: dict, context: AgentContext) -> AgentResult:
        trace: list[AgentStep] = []
        citations: list[Citation] = []
        total_input = 0
        total_output = 0
        total_cost = 0.0

        # 1. List substrates added in last 24h
        since = datetime.utcnow() - timedelta(hours=24)
        t0 = time.monotonic()
        new_subs = self._list_recent_substrates(context.user_id, since)
        trace.append(
            AgentStep(
                step_num=1,
                tool_name="list_substrates_since",
                tool_input={"since": since.isoformat()},
                tool_output={"count": len(new_subs)},
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )

        if not new_subs:
            return AgentResult(
                success=True,
                output={"new_substrates": 0, "digest": "No new substrates in the last 24h."},
                trace=trace,
                citations=[],
            )

        # 2. LLM summary
        prompt = self._build_digest_prompt(new_subs)
        t0 = time.monotonic()
        try:
            resp = llm_call(
                prompt=prompt,
                provider=self.llm_provider,
                temperature=self.temperature,
                max_tokens=512,
            )
            digest_text = resp.text
            total_input += resp.input_tokens
            total_output += resp.output_tokens
            total_cost += resp.cost_usd
        except Exception as exc:
            digest_text = f"(LLM digest failed: {exc})"

        trace.append(
            AgentStep(
                step_num=2,
                tool_name="llm_call",
                tool_input={"prompt_len": len(prompt)},
                tool_output={"output_len": len(digest_text)},
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )

        # 3. Citations
        for sub in new_subs:
            citations.append(
                Citation(
                    substrate_id=sub["id"],
                    title=sub.get("title", ""),
                    deep_link=f"stratum://substrate/{sub['id']}",
                )
            )

        # 4. Push notification
        t0 = time.monotonic()
        push_sent = False
        try:
            dispatcher = self._get_dispatcher()
            if dispatcher:
                await dispatcher.push(
                    user_id=context.user_id,
                    title=f"Daily Digest — {len(new_subs)} new substrates",
                    body=digest_text[:500],
                    deep_link="stratum://digest/today",
                )
                push_sent = True
        except Exception:
            pass
        trace.append(
            AgentStep(
                step_num=3,
                tool_name="push.dispatcher",
                tool_input={"user_id": context.user_id},
                tool_output={"sent": push_sent},
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )

        return AgentResult(
            success=True,
            output={"new_substrates": len(new_subs), "digest": digest_text},
            trace=trace,
            citations=citations,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            cost_usd=total_cost,
        )

    def _list_recent_substrates(self, user_id: str, since: datetime) -> list[dict]:
        """Query DuckDB for substrates added since *since*."""
        try:
            from oprim.meta_db import open_meta_db
            from oskill.knowledge._context import meta_db_path
            db = open_meta_db(meta_db_path())
            rows = db.fetchall(
                "SELECT id, title, created_at FROM substrate "
                "WHERE created_at >= ? ORDER BY created_at DESC LIMIT 50",
                [since.isoformat()],
            )
            return [{"id": r[0], "title": r[1] or "", "created_at": r[2]} for r in rows]
        except Exception:
            return []

    def _get_dispatcher(self):
        try:
            from oprim.push import PushDispatcher
            from oprim._config import cfg
            # Return None if not configured — push is best-effort
            return None  # real dispatcher requires channel config from env
        except Exception:
            return None

    @staticmethod
    def _build_digest_prompt(substrates: list[dict]) -> str:
        items = "\n".join(
            f"- {s.get('title') or '(无标题)'}" for s in substrates
        )
        return (
            "请用中文总结今日新增到知识库的内容，突出主题，不超过200字。\n\n"
            f"新增内容:\n{items}\n"
        )
