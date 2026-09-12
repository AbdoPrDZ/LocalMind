"""Shared chat service used by every interface (cmd, web, api, desktop).

A single place that boots the local model, database and agent, persists the
conversation (``Chat`` + ``Message``) and drives the LLM with a running chat
context rather than the full message history. Any interface only needs::

    chat = Chat.create()
    answer = chat.send("list all projects")

Chats and their messages are stored in the database, so a conversation can be
resumed later with ``Chat.load(chat_id)``.
"""

import os
import re
import sys
from typing import List, Optional
from collections.abc import Generator

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.env import ENV  # noqa: E402

ENV.init()

from sqlalchemy import func, select  # noqa: E402

from database import get_session, init_db  # noqa: E402
from models.chat import Chat as ChatRecord  # noqa: E402
from models.message import Message as MessageRecord  # noqa: E402
from services.global_context import build_global_context  # noqa: E402
from services.settings import resolve_model, resolve_provider  # noqa: E402
from services.usage import UsageService  # noqa: E402
from tools.memory import build_memory_tools  # noqa: E402
from tools.model import build_crud_tools  # noqa: E402
from utils.agent import Agent, clean_answer  # noqa: E402
from utils.llm import get_llm  # noqa: E402
from utils.tool import Tool  # noqa: E402

SYSTEM_PROMPT = ENV.get_system_prompt("You are a helpful assistant that can access the local database and perform CRUD operations.")

# Upper bound on the persisted chat context so it can never grow unbounded.
MAX_CONTEXT_CHARS = 12_000

# Context description appended to the system prompt. The current chat context
# is injected into the `{context}` placeholder; it replaces the full message
# history as the model's memory of the conversation.
CONTEXT_INSTRUCTIONS = """
You are talking in a persistent chat. You maintain a running summary of the conversation, called the CHAT CONTEXT. It replaces the full message history as your memory.

CHAT CONTEXT:
{context}

Use the CHAT CONTEXT above as your memory of everything said earlier in this chat.

The <context>...</context> block is INTERNAL bookkeeping between you and the app — the user never sees it. Never mention, announce, or narrate that you are saving, updating, or maintaining a context or memory; just do it silently and answer the user's question directly.

After EVERY answer, append the COMPLETE updated CHAT CONTEXT as the very LAST part of your reply, wrapped in exactly the lowercase tags <context>...</context>. Nothing else may be inside the tags. Never place the tags anywhere else in the reply and never wrap them in code fences or markdown.
"""

CONTEXT_TAG_RE = re.compile(r"<context>(.*?)</context>", re.DOTALL | re.IGNORECASE)

#: Short-prompt used to ask the model for a chat title on the first exchange.
TITLE_PROMPT = (
  "Write a very short title for this chat (4-8 words max, no quotes, no "
  "punctuation). Reply with ONLY the title.\n\nUser: {message}"
)


def _limit_context(context: str) -> str:
  """Clamp the running context so it cannot grow indefinitely.

  Keeps the most recent portion when it exceeds ``MAX_CONTEXT_CHARS``.
  """
  context = context.strip()
  if len(context) <= MAX_CONTEXT_CHARS:
    return context
  return context[-MAX_CONTEXT_CHARS:].strip()


def _extract_context(answer: str) -> tuple[Optional[str], str]:
  """Split a raw reply into the new chat context and the visible answer.

  Returns ``(new_context, clean_answer)``; ``new_context`` is ``None`` when the
  reply contains no ``<context>...</context>`` block.
  """
  match = CONTEXT_TAG_RE.search(answer or "")
  if match is None:
    return None, answer
  new_context = _limit_context(match.group(1))
  clean = CONTEXT_TAG_RE.sub("", answer).strip()
  return new_context, clean


def _stream_strip_context(
  chunks: Generator[str, None, None],
  context_out: list,
) -> Generator[str, None, None]:
  """Yield a streamed answer with any ``<context>...</context>`` block removed.

  The extracted context (or ``None``) is stored in ``context_out[0]``. A small
  buffer is retained so tags split across chunk boundaries are never emitted
  to the user, and an unclosed ``<context>`` block is captured rather than
  leaked into the visible answer.
  """
  buffer = ""
  context_buffer = ""
  in_context = False
  open_tag = "<context>"
  close_tag = "</context>"
  hold_size = max(len(open_tag), len(close_tag)) - 1

  for chunk in chunks:
    if not chunk:
      continue
    buffer += chunk

    while buffer:
      if in_context:
        end = buffer.lower().find(close_tag)
        if end == -1:
          if len(buffer) > hold_size:
            context_buffer += buffer[:-hold_size]
            buffer = buffer[-hold_size:]
          break
        context_buffer += buffer[:end]
        buffer = buffer[end + len(close_tag):]
        in_context = False
        context_out[0] = _limit_context(context_buffer)
        context_buffer = ""
        continue

      start = buffer.lower().find(open_tag)
      if start == -1:
        if len(buffer) > hold_size:
          safe = buffer[:-hold_size]
          buffer = buffer[-hold_size:]
          yield safe
        break
      if start:
        yield buffer[:start]
      buffer = buffer[start + len(open_tag):]
      in_context = True

  if not in_context:
    if buffer:
      yield buffer
  else:
    context_buffer += buffer
    context_out[0] = _limit_context(context_buffer)


def _fallback_title(message: str) -> Optional[str]:
  """A plain-text title from the first user message (no LLM call)."""
  title = re.sub(r"\s+", " ", message).strip()
  return title[:60] if title else None


def _generated_title(message: str) -> tuple[Optional[str], Optional[dict]]:
  """Ask the active LLM for a short chat title.

  Returns ``(title, usage)``; *usage* is the token count of the tiny
  title-generating call so accounting stays complete, ``None`` when the call
  failed or produced nothing to show.
  """
  try:
    llm = get_llm()
    result = llm.create_chat_completion(
      messages=[{"role": "user", "content": TITLE_PROMPT.format(message=message[:200])}],
      tools=None,
      max_tokens=24,
    )
    content = result["choices"][0]["message"].get("content") or ""
    content = clean_answer(content).strip().strip("'\"“”`").strip()
    return (content[:60] or None), result.get("usage")
  except Exception:
    return None, None


def _build_agent(
  system_prompt: Optional[str] = None,
  max_tokens: int = 1024,
) -> Agent:
  ENV.init()
  init_db()
  return Agent(
    tools=build_crud_tools() + build_memory_tools(),
    system_prompt=system_prompt or SYSTEM_PROMPT,
    max_tokens=max_tokens,
  )


class Chat:
  """A persisted conversation with the local assistant.

  Owns a ``ChatRecord`` row (with a running ``context`` summary) and drives the
  agent with the chat context instead of the full history: every ``send()``
  saves the user message, sends the system prompt + current chat context + the
  new user message to the LLM, then saves the assistant reply. If the reply
  contains a ``<context>...</context>`` block, it is extracted, persisted as
  the new chat context, and stripped from the visible answer. Example::

      chat = Chat.create()
      answer = chat.send("list all projects")
      response = chat.send("and the tasks too?")   # uses the chat context

      existing = Chat.load(chat.id)                 # resume later
  """

  def __init__(self, record: ChatRecord, agent: Agent, usage_session_id: int | None = None) -> None:
    self.record = record
    self.agent = agent
    self._usage_session_id = usage_session_id
    self._titled = False

  @classmethod
  def create(
    cls,
    title: Optional[str] = None,
    system_prompt: Optional[str] = None,
    max_tokens: int = 1024,
  ) -> "Chat":
    """Boot the environment/database, build the agent, start a new chat."""
    agent = _build_agent(system_prompt, max_tokens)

    session = get_session()
    try:
      record = ChatRecord(title=title)
      session.add(record)
      session.commit()
      session.refresh(record)
      session.expunge(record)
    finally:
      session.close()

    provider = resolve_provider()
    usage_session_id = UsageService.start_session(
      record.id,
      provider,
      resolve_model(provider),
    )

    return cls(record, agent, usage_session_id)

  @classmethod
  def load(
    cls,
    chat_id: int,
    system_prompt: Optional[str] = None,
    max_tokens: int = 1024,
  ) -> "Chat":
    """Resume an existing chat by id (raises ``ValueError`` if unknown)."""
    agent = _build_agent(system_prompt, max_tokens)

    session = get_session()
    try:
      record = session.get(ChatRecord, chat_id)
      if record is None:
        raise ValueError(
          f"Chat {chat_id} not found. Registered chats: "
          f"{[c.id for c in session.scalars(select(ChatRecord))]}"
        )
      session.expunge(record)
    finally:
      session.close()

    provider = resolve_provider()
    usage_session_id = UsageService.start_session(
      record.id,
      provider,
      resolve_model(provider),
    )

    return cls(record, agent, usage_session_id)

  @property
  def id(self) -> int:
    return self.record.id

  @property
  def title(self) -> Optional[str]:
    return self.record.title

  @property
  def tools(self) -> List[Tool]:
    return self.agent.tools

  @property
  def history(self) -> List[dict]:
    """All persisted messages for this chat, in order.

    Each entry is a chat-completion message: ``{"role": ..., "content": ...}``
    with role ``"user"`` or ``"assistant"``.
    """
    session = get_session()
    try:
      rows = session.scalars(
        select(MessageRecord)
        .where(MessageRecord.chat_id == self.id)
        .order_by(MessageRecord.id)
      ).all()
      return [{"role": row.role, "content": row.content} for row in rows]
    finally:
      session.close()

  @property
  def context(self) -> Optional[str]:
    """The running chat context summary, or ``None`` if none exists yet."""
    return self.record.context

  @property
  def usage_session_id(self) -> Optional[int]:
    """The currently open usage session for this chat, or ``None``."""
    return self._usage_session_id

  def _record_usage(self, usage: dict) -> None:
    """Persist the tokens spent by the last ``send`` into the open session."""
    if self._usage_session_id is None:
      return
    if not usage.get("total_tokens"):
      return
    UsageService.record(
      self._usage_session_id,
      usage.get("prompt_tokens", 0),
      usage.get("completion_tokens", 0),
    )

  @staticmethod
  def list_chats(limit: int = 50) -> list[dict]:
    """Return a recent list of chats with message counts (newest first)."""
    session = get_session()
    try:
      counts = dict(
        session.execute(
          select(
            MessageRecord.chat_id,
            func.count(MessageRecord.id),
          ).group_by(MessageRecord.chat_id)
        ).all()
      )
      rows = session.scalars(
        select(ChatRecord).order_by(ChatRecord.id.desc()).limit(limit)
      ).all()
      return [
        {
          "id": row.id,
          "title": row.title,
          "created_at": row.created_at,
          "context": row.context,
          "messages": counts.get(row.id, 0),
        }
        for row in rows
      ]
    finally:
      session.close()

  def reopen_usage(self, provider: str, model: str) -> None:
    """Close the current usage session and open a new one (provider switch).

    Used when the user runs ``/select model`` at runtime: the old session
    is stamped with ``ended_at`` and totals persist; the new session
    accounts for the new provider/model.
    """
    if self._usage_session_id is not None:
      UsageService.close_session(self._usage_session_id)
    self._usage_session_id = UsageService.start_session(self.id, provider, model)

  def close(self) -> None:
    """Close the chat's usage session (idempotent), if one is open.

    Interfaces call this when the conversation ends (e.g. on ''exit''),
    which sets the session's ``ended_at`` timestamp. Token totals remain.
    """
    if self._usage_session_id is not None:
      UsageService.close_session(self._usage_session_id)
      self._usage_session_id = None

  def usage_summary(self) -> dict:
    """Current totals for this chat's open session plus global aggregates.

    Returns ``None`` entries when the session is already closed or unknown.
    """
    session = None
    if self._usage_session_id is not None:
      session = UsageService.get_session(self._usage_session_id) or None
    return {
      "session": session,
      "chat_totals": UsageService.totals_for_chat(self.id),
      "global_totals": UsageService.totals(),
      "by_model": UsageService.totals_by_model(),
    }

  def _system_prompt(self) -> str:
    """The system prompt plus a global-memory snapshot and the chat-context instructions."""
    sections = [self.agent.system_prompt]

    global_context = build_global_context(current_chat_id=self.id)
    if global_context:
      sections.append(global_context.strip())

    bare = "no context yet" if not self.record.context else self.record.context
    sections.append(CONTEXT_INSTRUCTIONS.format(context=bare).strip())

    return "\n\n".join(sections)

  def _set_context(self, content: str) -> None:
    """Persist a new (size-limited) chat context summary for this chat."""
    content = _limit_context(content)
    session = get_session()
    try:
      record = session.get(ChatRecord, self.id)
      if record is not None:
        record.context = content
        session.commit()
        session.refresh(record)
    finally:
      session.close()
    self.record.context = content

  def _add_message(self, role: str, content: str) -> None:
    session = get_session()
    try:
      session.add(MessageRecord(chat_id=self.id, role=role, content=content))
      session.commit()
    finally:
      session.close()

  def _first_user_message(self, fallback: str) -> str:
    """The earliest persisted user message, used as the title-generation basis."""
    try:
      for row in self.history:
        if row["role"] == "user":
          return row["content"]
    except Exception:
      pass
    return fallback

  def _set_title(self, title: str) -> None:
    self.record.title = title
    session = get_session()
    try:
      record = session.get(ChatRecord, self.id)
      if record is not None:
        record.title = title
        session.commit()
    finally:
      session.close()

  def _ensure_title(self, user_message: str) -> None:
    """Generate and persist a title once, on the first real exchange."""
    if self.record.title or self._titled:
      return
    self._titled = True
    basis = self._first_user_message(user_message)
    title, usage = _generated_title(basis)
    if usage:
      self._record_usage(usage)
    if not title:
      title = _fallback_title(basis)
    if title:
      self._set_title(title)

  def send(self, user_message: str) -> str:
    """Persist and send a new user message, return the assistant's reply.

    Sends the system prompt + current chat context + the new user message
    only (no full history); the LLM may return an updated ``<context>`` block,
    which is extracted, persisted and stripped from the visible answer. If the
    LLM call fails, the user message stays persisted but the exception is
    re-raised (no assistant message is saved).
    """
    self._add_message("user", user_message)

    messages = [
      {"role": "system", "content": self._system_prompt()},
      {"role": "user", "content": user_message},
    ]

    answer = self.agent.run(messages)

    self._record_usage(self.agent.take_usage())

    new_context, clean = _extract_context(answer)
    if new_context is not None:
      self._set_context(new_context)

    self._add_message("assistant", clean)
    self._ensure_title(user_message)
    return clean

  def send_stream(self, user_message: str) -> Generator[str, None, None]:
    """Same as ``send`` but yields the assistant's reply in streaming chunks.

    The user message is persisted first and only the current chat context is
    sent with it (no full history). Any ``<context>...</context>`` block in
    the reply is stripped from the stream, extracted and persisted, and the
    cleaned reply is saved as soon as streaming finishes. Usage::

        chat = Chat.create()
        for chunk in chat.send_stream("list all projects"):
          sys.stdout.write(chunk)
          sys.stdout.flush()
    """
    self._add_message("user", user_message)

    messages = [
      {"role": "system", "content": self._system_prompt()},
      {"role": "user", "content": user_message},
    ]

    context_out: list[Optional[str]] = [None]
    parts: list[str] = []
    completed = False
    try:
      for chunk in _stream_strip_context(self.agent.run_stream(messages), context_out):
        if chunk:
          parts.append(chunk)
          yield chunk
      completed = True
    finally:
      # Only persist when generation actually finished; a failed/interrupted
      # stream leaves the user message persisted but saves no assistant reply
      # or context update. Usage is recorded once the stream completes.
      if completed:
        if parts:
          self._add_message("assistant", "".join(parts).strip())
        self._record_usage(self.agent.take_usage())
        if context_out[0] is not None:
          self._set_context(context_out[0])
        self._ensure_title(user_message)
