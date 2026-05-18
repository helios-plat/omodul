"""Build audit_evidence dict from agent outputs.

Matches HELIVEX_PHASE3_LLM_INTEGRATION §4.1 spec.
"""
from __future__ import annotations

import json


def build_audit_evidence(bull: dict, bear: dict, ref: dict) -> dict:
    """Aggregate 3 agent outputs into audit_evidence dict."""
    return {
        "stack_calls": [
            {"function": "oskill.llm_agent.bull_analyst", "args_hash": bull["prompt_hash_hex"]},
            {"function": "oskill.llm_agent.bear_analyst", "args_hash": bear["prompt_hash_hex"]},
            {"function": "oskill.llm_agent.referee", "args_hash": ref["prompt_hash_hex"]},
        ],
        "llm_reasoning_trace": _format_trace(bull, bear, ref),
        "llm_factor_dsl": json.dumps({
            "bull_confidence": bull["confidence"],
            "bear_confidence": bear["confidence"],
            "referee_factor": ref["factor_value"],
            "referee_verdict": ref["verdict"],
            "prompt_version": bull.get("prompt_version", "unknown"),
        }),
        "llm_consensus_votes": {
            "bull": bull["confidence"] / 100.0,
            "bear": bear["confidence"] / 100.0,
            "referee": ref["factor_value"],
        },
        "llm_input_tokens": bull["input_tokens"] + bear["input_tokens"] + ref["input_tokens"],
        "llm_output_tokens": bull["output_tokens"] + bear["output_tokens"] + ref["output_tokens"],
        "llm_cost_usd": bull["cost_usd"] + bear["cost_usd"] + ref["cost_usd"],
        "llm_model_id": ref["model_id"],
        "llm_elapsed_ms_total": bull["elapsed_ms"] + bear["elapsed_ms"] + ref["elapsed_ms"],
        "llm_parse_failures": {
            "bull": bull["parse_failed"],
            "bear": bear["parse_failed"],
            "referee": ref["parse_failed"],
        },
    }


def _format_trace(bull: dict, bear: dict, ref: dict) -> str:
    return (
        f"=== BULL ANALYST (confidence={bull['confidence']:.0f}) ===\n"
        f"{bull['raw_content']}\n\n"
        f"=== BEAR ANALYST (confidence={bear['confidence']:.0f}) ===\n"
        f"{bear['raw_content']}\n\n"
        f"=== REFEREE (factor={ref['factor_value']:.3f}, verdict={ref['verdict']}) ===\n"
        f"{ref['raw_content']}\n"
    )
