"""Structured per-chat state persisted as JSON in ``chats.state``.

The engine reads this state each turn and the app persists a merged summary
back into it. Structured fields (objective, decisions, ...) start empty and
grow over time; ``last_summary`` is the model-maintained condensed summary
extracted from the ``<context>`` block.
"""

import json

STATE_KEYS = (
  "objective",
  "current_topic",
  "decisions",
  "constraints",
  "open_questions",
  "important_entities",
  "last_summary",
)

SCALAR_KEYS = {"objective", "current_topic", "last_summary"}
LIST_KEYS = {"decisions", "constraints", "open_questions", "important_entities"}


def empty_state() -> dict:
  return {
    "objective": "",
    "current_topic": "",
    "decisions": [],
    "constraints": [],
    "open_questions": [],
    "important_entities": [],
    "last_summary": "",
  }


def parse_state(raw: str | None) -> dict:
  """Tolerant JSON parse; returns a pristine empty state on any failure."""
  if not raw:
    return empty_state()
  try:
    data = json.loads(raw)
  except (TypeError, ValueError):
    return empty_state()
  if not isinstance(data, dict):
    return empty_state()

  state = empty_state()
  for key in STATE_KEYS:
    value = data.get(key)
    if key in SCALAR_KEYS:
      if isinstance(value, str):
        state[key] = value
    elif key in LIST_KEYS:
      if isinstance(value, list):
        state[key] = [item for item in value if isinstance(item, str)]
  return state


def merge_summary(state: dict, summary: str) -> dict:
  """Persist the model-updated condensed summary into the state doc."""
  state = dict(state)
  state["last_summary"] = summary or ""
  return state


def serialize(state: dict) -> str:
  return json.dumps(state, ensure_ascii=False)