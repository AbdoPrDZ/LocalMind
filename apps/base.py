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

from sqlalchemy import select  # noqa: E402

from database import get_session, init_db  # noqa: E402
from models.chat import Chat as ChatRecord  # noqa: E402
from models.message import Message as MessageRecord  # noqa: E402
from tools.model import build_crud_tools  # noqa: E402
from utils.agent import Agent  # noqa: E402
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

After EVERY answer, append the COMPLETE updated CHAT CONTEXT as the very LAST part of your reply, wrapped in <context>...</context> tags. Nothing else may be inside the tags. The updated context reflects everything said in this exchange.
"""

CONTEXT_TAG_RE = re.compile(r"<context>(.*?)</context>", re.DOTALL | re.IGNORECASE)


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
        end = buffer.find(close_tag)
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

      start = buffer.find(open_tag)
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


def _build_agent(
  system_prompt: Optional[str] = None,
  max_tokens: int = 1024,
) -> Agent:
  ENV.init()
  init_db()
  return Agent(
    tools=build_crud_tools(),
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

  def __init__(self, record: ChatRecord, agent: Agent) -> None:
    self.record = record
    self.agent = agent

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
    finally:
      session.close()

    return cls(record, agent)

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

    return cls(record, agent)

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

  def _system_prompt(self) -> str:
    """The base system prompt plus the context-description instructions."""
    bare = "no context yet" if not self.record.context else self.record.context
    return self.agent.system_prompt + CONTEXT_INSTRUCTIONS.format(context=bare)

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

    new_context, clean = _extract_context(answer)
    if new_context is not None:
      self._set_context(new_context)

    self._add_message("assistant", clean)
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
      # or context update.
      if completed and parts:
        self._add_message("assistant", "".join(parts).strip())
      if completed and context_out[0] is not None:
        self._set_context(context_out[0])