"""Stratum builtin agents — auto-imported to register them."""
from omodul.knowledge.agents.builtin.daily_digest import DailyDigestAgent
from omodul.knowledge.agents.builtin.knowledge_curator import KnowledgeCuratorAgent
from omodul.knowledge.agents.builtin.lint_bot import LintBotAgent
from omodul.knowledge.agents.builtin.reading_companion import ReadingCompanionAgent
from omodul.knowledge.agents.builtin.translation_worker import TranslationWorkerAgent

__all__ = [
    "KnowledgeCuratorAgent",
    "DailyDigestAgent",
    "ReadingCompanionAgent",
    "TranslationWorkerAgent",
    "LintBotAgent",
]
