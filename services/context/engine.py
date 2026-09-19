"""ContextEngine: assemble one send's prompt from app-owned memory.

The engine reads from the database (recent turns, structured state), retrieves
the relevant global memories and old-history entries, and packs everything into
a token-budgeted chat-completion message list. ``apps/base.py``'s ``Chat``
service calls it and stays responsible for persistence, streaming, and the
``<context>`` extraction loop.
"""

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select

from database import get_session
from models.chat import Chat as ChatRecord
from models.message import Message
from services.context.budget import ContextBudget, estimate_tokens
from services.context.formatter import (
  build_system_section,
  format_memories_block,
  format_state_block,
)
from services.context.retrieval import retrieve_history, retrieve_memories
from services.context.state import empty_state, parse_state
from utils.env import ENV

DEFAULT_CONTEXT_WINDOW = 4096
DEFAULT_RECENT_TURNS = 6
DEFAULT_OUTPUT_RESERVE = 1024


def _context_window() -> int:
  raw = ENV.get("LLM_LOCAL_CONTEXT_WINDOW", default=str(DEFAULT_CONTEXT_WINDOW)) or ""
  try:
    return max(int(raw.strip()), 256)
  except ValueError:
    return DEFAULT_CONTEXT_WINDOW


def _recent_messages(chat_id: int, turns: int) -> list[dict]:
  """Last ``turns`` user/assistant turns in order (the newest is the last row)."""
  session = get_session()
  try:
    rows = session.scalars(
      select(Message)
      .where(Message.chat_id == chat_id)
      .order_by(Message.id.desc())
      .limit(turns)
    ).all()
  finally:
    session.close()
  return [
    {"id": row.id, "role": row.role, "content": row.content}
    for row in reversed(rows)
  ]


def _load_state(chat_id: int) -> dict:
  session = get_session()
  try:
    record = session.get(ChatRecord, chat_id)
    raw = record.state if record is not None else None
  finally:
    session.close()
  return parse_state(raw)


@dataclass
class ContextPackage:
  """Everything the engine assembled for one ``send``."""

  chat_id: int
  user_message: str
  recent_messages: list[dict]
  memories: list[dict]
  history: list[dict]
  state: dict
  budget: ContextBudget
  messages: list[dict] = field(default_factory=list)

  def to_messages(self) -> list[dict]:
    return self.messages

  def debug(self) -> dict:
    sections = {
      "system": sum(estimate_tokens(m["content"]) for m in self.messages if m["role"] == "system"),
      "recent_turns": sum(estimate_tokens(m["content"]) for m in self.messages if m["role"] != "system"),
    }
    return {
      "chat_id": self.chat_id,
      "budget": self.budget.debug(),
      "sections_tokens": sections,
      "memories": self.memories,
      "history": self.history,
      "state": self.state,
    }


def debug_context(chat_id: int, context_window: int | None = None) -> ContextPackage:
  """Build the context engine package for inspection without sending a message.

  Uses the last persisted user message as the retrieval query so the debugger
  shows exactly what the next reply would retrieve (Phase 5).
  """
  messages = _recent_messages(chat_id, 1)
  user_message = messages[-1]["content"] if messages else ""
  engine = ContextEngine(model_context_window=context_window)
  return engine.build(
    chat_id=chat_id,
    user_message=user_message,
    base_prompt="",
    instructions="",
  )


class ContextEngine:

  def __init__(
    self,
    model_context_window: Optional[int] = None,
    reserved_output_tokens: int = DEFAULT_OUTPUT_RESERVE,
    recent_turns: int = DEFAULT_RECENT_TURNS,
    memory_limit: int = 5,
    history_limit: int = 3,
  ) -> None:
    self.model_context_window = model_context_window or _context_window()
    self.reserved_output_tokens = reserved_output_tokens
    self.recent_turns = recent_turns
    self.memory_limit = memory_limit
    self.history_limit = history_limit

  def build(
    self,
    chat_id: int,
    user_message: str,
    base_prompt: str,
    instructions: str,
  ) -> ContextPackage:
    budget = ContextBudget(self.model_context_window, self.reserved_output_tokens)
    state = _load_state(chat_id)
    recent = _recent_messages(chat_id, self.recent_turns)
    recent_ids = {entry["id"] for entry in recent}

    memories = retrieve_memories(user_message, limit=self.memory_limit)
    history = retrieve_history(
      user_message,
      chat_id,
      limit=self.history_limit,
      exclude_message_ids=recent_ids,
    )

    memories_block = format_memories_block(memories)
    state_block = format_state_block(state, chat_id)

    # Shrink lower-priority sections first if the system message is too big.
    system_candidates = [
      build_system_section(base_prompt, memories_block, state_block, instructions),
      build_system_section(base_prompt, memories_block, "", instructions),
      build_system_section(base_prompt, "", "", instructions),
    ]
    system_section = next(
      (section for section in system_candidates if budget.fits(section)),
      system_candidates[-1],
    )
    budget.consume(system_section)
    messages: list[dict] = [{"role": "system", "content": system_section}]

    # Recent turns newest-first so the current user message always lands; the
    # accepted set is re-ordered chronologically afterwards.
    accepted: list[dict] = []
    for entry in reversed(recent):
      content = entry["content"]
      if budget.fits(content):
        budget.consume(content)
        accepted.append({"id": entry["id"], "role": entry["role"], "content": content})

    if not accepted and recent:
      # The user message alone exceeds the input budget: send what fits.
      content = budget.consume(recent[-1]["content"])
      if content:
        accepted.append({"id": recent[-1]["id"], "role": recent[-1]["role"], "content": content})

    accepted.sort(key=lambda entry: entry["id"])
    messages.extend(
      {"role": entry["role"], "content": entry["content"]}
      for entry in accepted
    )

    return ContextPackage(
      chat_id=chat_id,
      user_message=user_message,
      recent_messages=[{"role": e["role"], "content": e["content"]} for e in accepted],
      memories=memories,
      history=history,
      state=state,
      budget=budget,
      messages=messages,
    )