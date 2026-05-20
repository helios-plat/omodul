"""View applier — pure merge of view defaults into search params.

No DB access. Caller resolves view_id → view dict first.
User-provided params always take precedence over view defaults.
"""
from __future__ import annotations


def apply_view(view: dict, params: dict) -> dict:
    """Merge *view* defaults into *params*; params win on conflict.

    Returns a new dict — does not mutate either argument.

    Resolved keys:
      medium_filter  ← view.default_filter.medium  (list[str])
      domain_filter  ← view.default_filter.domain  (list[str])
      time_range     ← view.default_filter.time_range  (str | None)
      llm_provider   ← view.default_llm.provider
      llm_model      ← view.default_llm.model
      system_prompt  ← view.default_system_prompt
    """
    if not view:
        return dict(params)

    merged = dict(params)
    vf = view.get("default_filter") or {}

    # medium filter — only set if caller didn't pass one
    if "medium_filter" not in merged and vf.get("medium"):
        merged["medium_filter"] = list(vf["medium"])

    # domain filter
    if "domain_filter" not in merged and vf.get("domain"):
        merged["domain_filter"] = list(vf["domain"])

    # time range (work_log uses "last_30d")
    if "time_range" not in merged and vf.get("time_range"):
        merged["time_range"] = vf["time_range"]

    # LLM overrides
    vllm = view.get("default_llm") or {}
    if "llm_provider" not in merged and vllm.get("provider"):
        merged["llm_provider"] = vllm["provider"]
    if "llm_model" not in merged and vllm.get("model"):
        merged["llm_model"] = vllm["model"]

    # system prompt
    if "system_prompt" not in merged and view.get("default_system_prompt"):
        merged["system_prompt"] = view["default_system_prompt"]

    return merged
