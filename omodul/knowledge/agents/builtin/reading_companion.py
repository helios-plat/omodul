"""ReadingCompanionAgent — conversational Q&A grounded in user substrate library."""

from __future__ import annotations

import time

from oprim.llm import llm_call
from oskill.hybrid_search import hybrid_search

from omodul.knowledge.agents.base import Agent, AgentContext, AgentResult, AgentStep, Citation
from omodul.knowledge.agents.registry import register_agent


@register_agent
class ReadingCompanionAgent(Agent):
    name = "reading_companion"
    description = "Answers questions using the user's substrate library (hybrid search + LLM)."
    allowed_tools = [
        "oskill.knowledge.hybrid_search",
        "oprim.llm.llm_call",
    ]

    async def run(self, params: dict, context: AgentContext) -> AgentResult:
        question = params.get("question", "").strip()
        if not question:
            raise ValueError("'question' param is required")

        trace: list[AgentStep] = []
        citations: list[Citation] = []
        total_input = 0
        total_output = 0
        total_cost = 0.0

        # 1. Hybrid search — corpus_id required by hybrid_search; derive from user_id
        corpus_id = params.get("corpus_id") or f"user_{context.user_id}"
        t0 = time.monotonic()
        results = await hybrid_search(
            query=question,
            corpus_id=corpus_id,
            top_k=5,
            mode="augmented",
            return_citations=True,
        )
        trace.append(
            AgentStep(
                step_num=1,
                tool_name="hybrid_search",
                tool_input={"query": question, "limit": 5},
                tool_output={"results": len(results)},
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )

        # 2. Build context for LLM
        substrate_ctx = "\n\n".join(
            f"[{r.id}] {getattr(r, 'title', '')} \n{getattr(r, 'highlight', '')}" for r in results
        )
        prompt = (
            f"根据用户知识库内容回答问题。每个观点引用来源（用 [substrate_id] 标记）。\n\n"
            f"用户问题: {question}\n\n"
            f"知识库相关内容:\n{substrate_ctx}\n\n"
            f"回答（中文，简洁准确，必含来源标记）:"
        )

        # 3. LLM call
        t0 = time.monotonic()
        try:
            resp = llm_call(
                prompt=prompt,
                provider=self.llm_provider,
                temperature=self.temperature,
                max_tokens=1024,
            )
            answer = resp.text
            total_input += resp.input_tokens
            total_output += resp.output_tokens
            total_cost += resp.cost_usd
        except Exception as exc:
            answer = f"(LLM call failed: {exc})"

        trace.append(
            AgentStep(
                step_num=2,
                tool_name="llm_call",
                tool_input={"prompt_len": len(prompt)},
                tool_output={"answer_len": len(answer)},
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        )

        # 4. Citations (SearchResult.id is the substrate_id; .citation is dict or None)
        for r in results:
            cit = getattr(r, "citation", None)
            if isinstance(cit, dict):
                citations.append(
                    Citation(
                        substrate_id=cit.get("substrate_id", r.id),
                        title=r.title,
                        fragment_id=cit.get("fragment_id"),
                        deep_link=cit.get("deep_link"),
                    )
                )
            else:
                citations.append(Citation(substrate_id=r.id, title=r.title))

        return AgentResult(
            success=True,
            output={"answer": answer},
            trace=trace,
            citations=citations,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            cost_usd=total_cost,
        )
