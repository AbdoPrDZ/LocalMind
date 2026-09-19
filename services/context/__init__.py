"""Context Engine v2: token-budgeted, query-aware prompt assembly.

The old design asked the LLM to restate the *entire* conversation in a
``<context>`` block after every reply. The engine inverts that: LocalMind owns
the memory, and the engine assembles a bounded view of it per turn —

  base system prompt
  + query-relevant global memories
  + structured per-chat state
  + recent verbatim turns (incl. the current user message)

bounded by the model's context window (``remaining``/``budget.py``) instead of
character slicing the conversation summary.
"""

from services.context.budget import ContextBudget, estimate_tokens
from services.context.engine import ContextEngine, ContextPackage
from services.context.ranking import keyword_overlap, score_memory
from services.context.retrieval import retrieve_history, retrieve_memories
from services.context.state import empty_state, parse_state, serialize

__all__ = [
  "ContextBudget",
  "ContextEngine",
  "ContextPackage",
  "empty_state",
  "estimate_tokens",
  "keyword_overlap",
  "parse_state",
  "retrieve_history",
  "retrieve_memories",
  "score_memory",
  "serialize",
]