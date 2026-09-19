"""Shared chat service used by every interface (cmd, web, api, desktop).

A single place that boots the local model, database and agent, persists the
conversation (``Chat`` + ``Message``) and drives the LLM with a running chat
context rather than the full message history. Any interface only needs::

    chat = Chat.create()
    answer = chat.send("list all projects")

Chats and their messages are stored in the database, so a conversation can be
resumed later with ``Chat.load(chat_id)``.
"""

import json
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
from services.context import ContextEngine  # noqa: E402
from services.context.state import merge_summary, parse_state, serialize  # noqa: E402
from services.memory import MemoryService, normalize_content  # noqa: E402
from services.settings import resolve_model, resolve_provider  # noqa: E402
from services.usage import UsageService  # noqa: E402
from tools.ask import QuestionHandler, build_ask_tools  # noqa: E402
from tools.files import build_files_tools  # noqa: E402
from tools.memory import build_memory_tools  # noqa: E402
from tools.model import build_crud_tools  # noqa: E402
from tools.shell import build_shell_tools  # noqa: E402
from tools.system import build_system_tools  # noqa: E402
from tools.web import build_web_tools  # noqa: E402
from utils.agent import Agent, clean_answer  # noqa: E402
from utils.llm import get_llm  # noqa: E402
from utils.tool import Tool  # noqa: E402

SYSTEM_PROMPT = ENV.get_system_prompt("You are a helpful assistant that can access the local database and perform CRUD operations.")

# Upper bound on the persisted chat context so it can never grow unbounded.
MAX_CONTEXT_CHARS = 12_000

#: Effective budget for the model-maintained CHAT STATE summary. Recent verbatim
#: turns now come from the messages table, so the summary only needs the durable
#: facts — small by design (see services/context/engine.py).
MAX_STATE_CONTEXT_CHARS = 2_000


def _model_context_window() -> int:
  raw = ENV.get("LLM_LOCAL_CONTEXT_WINDOW", default="4096") or "4096"
  try:
    return max(int(raw.strip()), 256)
  except ValueError:
    return 4096

#: Tools whose output is folded into the chat context automatically when the
#: model sends no ``<context>`` block of its own (memory/sytem/clipboard reads
#: are excluded — they are either already persistent or transient).
CONTEXT_FOLD_EXCLUDED_TOOLS = frozenset({
  "search_global_memory",
  "get_memory",
  "get_chat_context",
  "search_chat_history",
  "current_datetime",
  "get_system_info",
  "clipboard_get",
  "clipboard_set",
})

#: Tools whose results are auto-captured into GLOBAL memory (bounded per entry
#: and deduplicated by the memory service), so durable knowledge survives chats.
#: Web retrieval tools (web_search/fetch_page) are deliberately excluded: their
#: results are transient search data, not durable knowledge, and auto-persisting
#: them pollutes memory. ask_user captures explicit answers; read_file captures
#: persistent project files.
AUTO_CAPTURE_TOOLS = frozenset({"ask_user", "read_file"})
MEMORY_CAPTURE_CHARS = 1_200
TOOL_CONTEXT_TAIL_BUDGET = 6_000
TOOL_RESULT_TRIM_CHARS = 600

# Context description appended to the system prompt. The engine renders the
# structured CHAT STATE as its own section and sends recent verbatim turns as
# separate messages, so this text only carries the protocol rules.
CONTEXT_INSTRUCTIONS = """
You are talking in a persistent chat. The app maintains a durable CHAT STATE for you — a condensed summary of the facts that should survive this conversation (decisions, preferences, constraints, open questions). Recent verbatim turns of the conversation are provided as the actual messages, so the CHAT STATE does NOT need to reproduce them.

Use the CHAT STATE section above as your memory of the durable facts of this conversation. When you answer, rely on both the recent messages and the CHAT STATE.

The <context>...</context> block is INTERNAL bookkeeping between you and the app — the user never sees it. Never mention, announce, or narrate that you are saving, updating, or maintaining a context or memory; just do it silently and answer the user's question directly.

After EVERY answer, append your updated CHAT STATE as the very LAST part of your reply, wrapped in exactly the lowercase tags <context>...</context>. The updated state must be a tight, compressed summary: keep decisions, preferences, constraints, open questions, and facts that matter; drop stale detail and trivia that the recent messages already cover. Nothing else may be inside the tags. Never place the tags anywhere else in the reply and never wrap them in code fences or markdown.
"""

CONTEXT_TAG_RE = re.compile(r"<context>(.*?)</context>", re.DOTALL | re.IGNORECASE)

#: Short-prompt used to ask the model for a chat title on the first exchange.
TITLE_PROMPT = (
  "Write a very short title for this chat (4-8 words max, no quotes, no "
  "punctuation). Reply with ONLY the title.\n\nUser: {message}"
)


def _limit_context(context: str, max_chars: int = MAX_CONTEXT_CHARS) -> str:
  """Clamp the running state so it cannot grow indefinitely.

  Keeps the most recent portion when it exceeds ``max_chars``.
  """
  context = context.strip()
  if len(context) <= max_chars:
    return context
  return context[-max_chars:].strip()


def _clean_text(text: Optional[str]) -> Optional[str]:
  """Drop lone surrogates / invalid UTF-16 units before persistence.

  Windows console input and, occasionally, model replies can carry unpaired
  surrogates; sqlite's UTF-8 driver rejects them on INSERT (``surrogates not
  allowed``). Valid emoji and all other unicode pass through untouched.
  """
  if text is None:
    return None
  return text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")


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


def _merge_contexts(
  previous: Optional[str],
  new_context: str,
  max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
  """Combine the stored state with the model's update without losing history.

  The chat state should condense the whole conversation's durable facts (the
  model is told ``CHAT STATE`` must stay compressed), but a weak model may
  still emit only the newest topic. Handling is therefore defensive:

  - No stored state yet → use the update as-is.
  - The update already contains the full stored state → trust it (replace).
  - Otherwise the model sent a partial/other-topic slice → append it to the
    stored state, dropping exact-duplicate lines to bound growth.
  """
  previous = (previous or "").strip()
  new_context = (new_context or "").strip()
  if not previous:
    return _limit_context(new_context, max_chars)
  if not new_context:
    return _limit_context(previous, max_chars)
  if normalize_content(new_context).find(normalize_content(previous)) != -1:
    return _limit_context(new_context, max_chars)

  combined = f"{previous}\n\n{new_context}"
  seen: set[str] = set()
  lines: list[str] = []
  for line in combined.splitlines():
    key = normalize_content(line)
    if not key or key in seen:
      continue
    seen.add(key)
    lines.append(line)
  return _limit_context("\n".join(lines), max_chars)


def _serialize_result(result) -> str:
  if isinstance(result, dict):
    return json.dumps(result, ensure_ascii=False, default=str)
  return str(result)


def _collapse_tool_result(name: str, result) -> str:
  """Compact a tool result into a durable, small summary (per-tool).

  Replaces the blind ``[:600]`` slice with purposeful extraction, so the
  synthesized chat state keeps the useful signal instead of raw blobs.
  """
  if not isinstance(result, dict):
    return _serialize_result(result)

  if name == "web_search":
    lines = [f"query: {result.get('query', '')}"]
    for item in (result.get("results") or [])[:5]:
      lines.append(f"- {item.get('title', '')} | {item.get('url', '')}")
    return "\n".join(lines)

  if name == "fetch_page":
    text = _clean_text(result.get("text", "")) or ""
    lines = [f"url: {result.get('url', '')}"]
    if text:
      lines.append(text[:400])
    links = (result.get("links") or [])[:10]
    if links:
      lines.append("links: " + ", ".join(links))
    return "\n".join(lines)

  if name == "read_file":
    content = _clean_text(result.get("content")) or ""
    if len(content) > 500:
      return content[:300] + "\n...[truncated]\n" + content[-200:]
    return content

  return _serialize_result(result)


def _synthesize_tool_context(results: list[dict]) -> Optional[str]:
  """Build a compact "what the model fetched this exchange" section.

  Returned text is appended to the chat context when the model emits no
  ``<context>`` block, so tool data is never lost between turns.
  """
  lines: list[str] = []
  total = 0
  for item in results:
    name = item.get("name")
    if not name or name in CONTEXT_FOLD_EXCLUDED_TOOLS:
      continue
    raw = _collapse_tool_result(name, item.get("result"))
    text = (raw or "")[:TOOL_RESULT_TRIM_CHARS].strip()
    if not text:
      continue
    line = f"- {name}: {text}"
    if lines and total + len(line) + 1 > TOOL_CONTEXT_TAIL_BUDGET:
      break
    lines.append(line)
    total += len(line) + 1
  if not lines:
    return None
  header = "RECENT TOOL RESULTS (facts gathered this exchange — remember and reuse them):"
  return f"{header}\n" + "\n".join(lines)


def _memory_capture_content(name: str, result) -> Optional[str]:
  """Render one tool result as a durable one-line fact, or ``None`` to skip."""
  if not isinstance(result, dict):
    return None
  if name == "ask_user" and result.get("answer"):
    return f'The user answered "{result["answer"]}".'
  if name == "fetch_page" and result.get("text"):
    return f"Fetched page {result.get('url', '')}: {result['text']}"[:MEMORY_CAPTURE_CHARS]
  if name == "web_search" and result.get("results"):
    return (
      f'Web search "{result.get("query", "")}": '
      f'{_serialize_result(result["results"])}'
    )[:MEMORY_CAPTURE_CHARS]
  if name == "read_file" and result.get("content"):
    return f"File {result.get('file', '')}: {result['content']}"[:MEMORY_CAPTURE_CHARS]
  return None


def _latest_user_message_id(chat_id: int) -> int | None:
  """Id of the newest persisted user message in a chat (provenance)."""
  session = get_session()
  try:
    return session.execute(
      select(MessageRecord.id)
      .where(MessageRecord.chat_id == chat_id, MessageRecord.role == "user")
      .order_by(MessageRecord.id.desc())
      .limit(1)
    ).scalar_one_or_none()
  finally:
    session.close()


def _capture_global_memories(results: list[dict], chat_id: int) -> None:
  """Auto-save durable tool findings into global memory (opt-out via AUTO_MEMORIZE=0).

  Low-importance ``fact`` entries, truncated and deduplicated by the memory
  service, so the knowledge survives the current chat. Failures are swallowed —
  capture must never break a reply.
  """
  enabled = (ENV.get("AUTO_MEMORIZE", default="1") or "1").strip().lower()
  if enabled not in {"1", "true", "yes"}:
    return
  source_message_id = _latest_user_message_id(chat_id)
  for item in results:
    name = item.get("name")
    if name not in AUTO_CAPTURE_TOOLS:
      continue
    content = _memory_capture_content(name, item.get("result"))
    if not content:
      continue
    content = _clean_text(content)
    if not content:
      continue
    try:
      MemoryService.create(
        type_="fact",
        content=content,
        importance=1,
        source_chat_id=chat_id,
        source_message_id=source_message_id,
      )
    except Exception:
      continue


def _tool_results(agent) -> list[dict]:
  """Safely read the agent's recorded tool results (may be a test stub)."""
  getter = getattr(agent, "take_tool_results", None)
  return getter() if callable(getter) else []


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
  question_handler: Optional[QuestionHandler] = None,
) -> Agent:
  """Build an agent with the full tool set an interface needs.

  ``question_handler`` is the app's blocking ``(question, options,
  allow_free_text) -> answer | None`` callback; it backs the ``ask_user`` tool
  and the user-approval step of ``run_command`` (see ``tools/ask.py``).
  """
  ENV.init()
  init_db()
  tools = build_crud_tools() + build_memory_tools()
  tools += build_files_tools()
  tools += build_web_tools()
  tools += build_system_tools()
  tools += build_ask_tools(question_handler)
  tools += build_shell_tools(question_handler)
  return Agent(
    tools=tools,
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
    question_handler: Optional[QuestionHandler] = None,
  ) -> "Chat":
    """Boot the environment/database, build the agent, start a new chat."""
    agent = _build_agent(system_prompt, max_tokens, question_handler)

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
    question_handler: Optional[QuestionHandler] = None,
  ) -> "Chat":
    """Resume an existing chat by id (raises ``ValueError`` if unknown)."""
    agent = _build_agent(system_prompt, max_tokens, question_handler)

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
    """Legacy system-prompt builder (kept for callers outside ``send``).

    ``Chat.send``/``send_stream`` assemble their prompts through the
    ContextEngine instead; this method is retained only for compatibility.
    """
    sections = [self.agent.system_prompt]

    global_context = build_global_context(current_chat_id=self.id)
    if global_context:
      sections.append(global_context.strip())

    bare = "no context yet" if not self.record.context else self.record.context
    sections.append(CONTEXT_INSTRUCTIONS.format(context=bare).strip())

    return "\n\n".join(sections)

  def _set_context(self, content: str) -> None:
    """Persist a new (merged, size-limited) chat state summary + JSON state.

    The legacy ``chats.context`` column mirrors ``state["last_summary"]`` so
    ``get_chat_context`` and the ``/context`` command keep working; the JSON
    ``chats.state`` document is the structured version the ContextEngine reads.
    """
    merged = _clean_text(
      _merge_contexts(self.record.context, content, max_chars=MAX_STATE_CONTEXT_CHARS)
    ) or ""
    state = merge_summary(
      parse_state(self.record.state),
      merged,
    )
    raw_state = serialize(state)
    session = get_session()
    try:
      record = session.get(ChatRecord, self.id)
      if record is not None:
        record.context = merged
        record.state = raw_state
        session.commit()
        session.refresh(record)
    finally:
      session.close()
    self.record.context = merged
    self.record.state = raw_state

  def _add_message(self, role: str, content: str) -> None:
    content = _clean_text(content) or ""
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
    basis = self._first_user_message(_clean_text(user_message) or "")
    title, usage = _generated_title(basis)
    if usage:
      self._record_usage(usage)
    if not title:
      title = _fallback_title(basis)
    if title:
      self._set_title(title)

  def _engine_messages(self, user_message: str) -> list[dict]:
    """Assemble the context-engine prompt for this turn.

    The engine reads the recent verbatim turns, structured chat state, and
    relevant global memories/history, then packs them under a token budget.
    """
    engine = ContextEngine(
      model_context_window=_model_context_window(),
      reserved_output_tokens=getattr(self.agent, "max_tokens", 1024),
    )
    return engine.build(
      chat_id=self.id,
      user_message=user_message,
      base_prompt=self.agent.system_prompt,
      instructions=CONTEXT_INSTRUCTIONS,
    ).to_messages()

  def send(self, user_message: str) -> str:
    """Persist and send a new user message, return the assistant's reply.

    The message list is assembled by the ContextEngine (recent turns + chat
    state + relevant memories/history under a token budget); the LLM may return
    an updated ``<context>`` block, which is extracted, persisted and stripped
    from the visible answer. If the LLM call fails, the user message stays
    persisted but the exception is re-raised (no assistant message is saved).
    """
    user_message = _clean_text(user_message) or ""
    self._add_message("user", user_message)

    answer = self.agent.run(self._engine_messages(user_message))

    self._record_usage(self.agent.take_usage())

    new_context, clean = _extract_context(answer)
    results = _tool_results(self.agent)
    if new_context is not None:
      self._set_context(new_context)
    else:
      tail = _synthesize_tool_context(results)
      if tail:
        self._set_context(tail)
    _capture_global_memories(results, self.id)

    self._add_message("assistant", clean)
    self._ensure_title(user_message)
    return clean

  def send_stream(self, user_message: str) -> Generator[str, None, None]:
    """Same as ``send`` but yields the assistant's reply in streaming chunks.

    The user message is persisted first and the ContextEngine assembles the
    prompt (recent turns + chat state + relevant memories under a token
    budget). Any ``<context>...</context>`` block in the reply is stripped from
    the stream, extracted and persisted, and the cleaned reply is saved as soon
    as streaming finishes. Usage::

        chat = Chat.create()
        for chunk in chat.send_stream("list all projects"):
          sys.stdout.write(chunk)
          sys.stdout.flush()
    """
    user_message = _clean_text(user_message) or ""
    self._add_message("user", user_message)

    messages = self._engine_messages(user_message)

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
        results = _tool_results(self.agent)
        if context_out[0] is not None:
          self._set_context(context_out[0])
        else:
          tail = _synthesize_tool_context(results)
          if tail:
            self._set_context(tail)
        _capture_global_memories(results, self.id)
        self._ensure_title(user_message)
