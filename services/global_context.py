"""Build a small, prompt-friendly global-context snapshot from memories.

Only the most important memories are included, grouped by type and capped by
character budget — the full memory table is never injected into the prompt.
"""

from typing import Optional

from services.memory import MemoryService

GLOBAL_CONTEXT_MAX_CHARS = 4000
GLOBAL_CONTEXT_MAX_ENTRIES = 12

_GROUP_LABELS = {
  "preference": "Important preferences",
  "decision": "Important decisions",
  "topic": "Known topics",
  "fact": "Important facts",
}


def build_global_context(
  max_chars: int = GLOBAL_CONTEXT_MAX_CHARS,
  max_entries: int = GLOBAL_CONTEXT_MAX_ENTRIES,
  current_chat_id: Optional[int] = None,
) -> str:
  """Return a bounded global-memory snapshot, or ``""`` when there is nothing to show."""
  memories = MemoryService.list(limit=max_entries)
  if not memories:
    return ""

  grouped: dict[str, list[str]] = {}
  for memory in memories:
    grouped.setdefault(memory["type"], []).append(memory["content"])

  lines: list[str] = []
  total = 0
  for type_ in _GROUP_LABELS:
    contents = grouped.get(type_)
    if not contents:
      continue
    block_lines = [_GROUP_LABELS[type_]] + [f"- {content}" for content in contents]
    block = "\n".join(block_lines)
    if lines and total + len(block) + 2 > max_chars:
      break
    lines.append(block)
    total += len(block) + 2

  if not lines:
    return ""

  header = ""
  if current_chat_id is not None:
    header = f"CURRENT CHAT ID: {current_chat_id}\n\n"

  return header + "GLOBAL MEMORY\n\n" + "\n\n".join(lines) + "\n"