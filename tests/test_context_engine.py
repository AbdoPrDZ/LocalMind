"""ContextEngine v2 tests: recent turns, budget, state, query-aware memory.

Small end-to-end drives — a real ``Chat`` service with a recording/stub agent
— prove the engine's mechanics: the current user message is never duplicated,
oldest turns are dropped under budget while durable CHAT STATE survives, and
query-relevant global memories are injected. Full semantic scenarios (T1-T5
from the blueprint) are asserted where the Phase 1 mechanism supports them;
supersession (T2) lands in Phase 2 and keyword/FTS retrieval quality in Phase 3.
"""

import json

from database import get_session
from models.chat import Chat as ChatRecord
from services.context import ContextEngine, estimate_tokens
from services.context.budget import ContextBudget
from services.context.state import empty_state, parse_state, serialize
from services.memory import MemoryService


class _RecordingAgent:
  system_prompt = "sys"

  def __init__(self, answer: str = "ok"):
    self.answer = answer
    self.received: list[dict] = []
    self.max_tokens = 1024

  def run(self, messages):
    self.received = [dict(message) for message in messages]
    return self.answer

  def run_stream(self, messages):
    self.received = [dict(message) for message in messages]
    yield self.answer

  def take_usage(self):
    return {}

  def take_tool_results(self):
    return []


class _ContextAgent(_RecordingAgent):
  def __init__(self):
    super().__init__("Fine.")
    self._first = True

  def run(self, messages):
    self.received = [dict(message) for message in messages]
    reply = "Fine.<context>Decision: deployment uses SQLite.</context>"
    self.answer = reply
    return reply


def _make_chat(db, agent=None):
  from apps.base import Chat

  session = get_session()
  record = ChatRecord(title=None)
  session.add(record)
  session.commit()
  session.refresh(record)
  session.expunge(record)
  session.close()
  return Chat(record, agent or _RecordingAgent(), usage_session_id=None)


# ---------------------------------------------------------------------------
# ContextBudget mechanics
# ---------------------------------------------------------------------------


def test_budget_math():
  budget = ContextBudget(model_context_window=4096, reserved_output_tokens=1024)
  assert budget.remaining_tokens == 4096 - 1024
  assert budget.remaining_chars == (4096 - 1024) * 4

  budget.consume("hello")  # 5 chars
  assert budget.remaining_chars == (4096 - 1024) * 4 - 5
  assert not budget.fits("x" * (budget.remaining_chars + 1))
  assert budget.fits("short")


def test_budget_consume_trims_from_end():
  budget = ContextBudget(model_context_window=1000, reserved_output_tokens=0)
  trimmed = budget.consume("a" * 500 + "TAIL")
  assert trimmed.endswith("TAIL")
  assert len(trimmed) <= 4000


def test_estimate_tokens_is_conservative():
  assert estimate_tokens("abcd") >= 1
  assert estimate_tokens("a" * 1000) == 250


# ---------------------------------------------------------------------------
# State (parse / serialize / merge)
# ---------------------------------------------------------------------------


def test_state_roundtrip_tolerant():
  assert parse_state(None) == empty_state()
  assert parse_state("not json") == empty_state()
  assert parse_state("42") == empty_state()

  state = empty_state()
  state["last_summary"] = "We chose SQLite."
  state["decisions"] = ["Use SQLite"]
  raw = serialize(state)
  assert parse_state(raw) == state


def test_state_ignores_unknown_keys():
  parsed = parse_state('{"last_summary": "x", "bogus": 1, "decisions": [1, "ok"]}')
  assert parsed["last_summary"] == "x"
  assert parsed["decisions"] == ["ok"]
  assert "bogus" not in parsed


# ---------------------------------------------------------------------------
# Recent turns: current message present, never duplicated, oldest dropped
# ---------------------------------------------------------------------------


def test_recent_turns_included_in_prompt(db):
  chat = _make_chat(db)
  chat.send("I prefer React Native.")
  chat.send("Tell me about the weather.")
  chat.send("What mobile framework do I prefer?")

  roles = [m["role"] for m in chat.agent.received]
  contents = [m["content"] for m in chat.agent.received]

  # The current user message is the last message, and appears exactly once.
  assert roles[-1] == "user" and contents[-1] == "What mobile framework do I prefer?"
  assert contents.count("What mobile framework do I prefer?") == 1
  # Earlier turns travel with the prompt (T1 mechanism: the model can answer).
  assert "I prefer React Native." in contents
  assert "Tell me about the weather." in contents
  # Every turn arrives after the system message.
  assert roles[0] == "system"


def test_budget_drops_oldest_turns(db):
  small = ContextEngine(
    model_context_window=1024,
    reserved_output_tokens=512,  # ~2048 chars input
    recent_turns=50,
  )
  chat_id = _create_chat_id(db)
  for i in range(20):
    _insert_message(chat_id, "user", f"turn{i} " + "x" * 300)

  package = small.build(
    chat_id=chat_id,
    user_message="turn19 " + "x" * 300,
    base_prompt="sys",
    instructions="instr",
  )

  assert package.messages[0]["role"] == "system"
  assert package.messages[-1]["content"].startswith("turn19")
  # Oldest turn dropped once the budget is exhausted.
  assert not any(m["content"].startswith("turn0 ") for m in package.messages)


# ---------------------------------------------------------------------------
# Query-aware global memory
# ---------------------------------------------------------------------------


def test_relevant_memories_injected(db):
  MemoryService.create("preference", "User prefers React Native CLI.", importance=3)

  chat = _make_chat(db)
  chat.send("Which CLI does the user prefer for React Native?")
  system = chat.agent.received[0]["content"]
  assert "RELEVANT GLOBAL MEMORIES" in system
  assert "React Native CLI" in system


def test_unrelated_query_skips_memories(db):
  MemoryService.create("preference", "User prefers React Native CLI.", importance=1)
  MemoryService.create("fact", "LocalMind uses SQLite.", importance=2)

  chat = _make_chat(db)
  chat.send("What is the weather today?")
  system = chat.agent.received[0]["content"]
  # Nothing matches "weather"; low/medium-importance memories are not injected.
  assert "RELEVANT GLOBAL MEMORIES" not in system


def test_high_importance_memory_always_considered(db):
  MemoryService.create("decision", "We decided to use OpenRouter.", importance=4)
  chat = _make_chat(db)
  chat.send("OpenRouter details, please.")
  system = chat.agent.received[0]["content"]
  assert "OpenRouter" in system


# ---------------------------------------------------------------------------
# Durable state survives long conversations (T5 mechanism)
# ---------------------------------------------------------------------------


def test_duration_state_survives_budget_pressure(db):
  chat = _make_chat(db)
  chat._set_context("Decision: deployment uses SQLite.")

  chat_id = chat.id
  for i in range(20):
    _insert_message(chat_id, "user", f"long turn {i} " + "z" * 400)

  engine = ContextEngine(model_context_window=2048, reserved_output_tokens=512)
  package = engine.build(
    chat_id=chat_id,
    user_message="long turn 19 " + "z" * 400,
    base_prompt="sys",
    instructions="instr",
  )

  system = package.messages[0]["content"]
  assert "Decision: deployment uses SQLite." in system
  # The newest turn is the final message; the history is not head-truncated.
  assert package.messages[-1]["content"].startswith("long turn 19")
  assert len(package.messages) < 21


def test_state_persisted_as_json_and_mirrored(db):
  chat = _make_chat(db, agent=_ContextAgent())
  chat.send("deploy this")

  session = get_session()
  try:
    record = session.get(ChatRecord, chat.id)
    state = parse_state(record.state)
    assert state["last_summary"] == "Decision: deployment uses SQLite."
    assert "SQLite" in record.context
  finally:
    session.close()


# ---------------------------------------------------------------------------
# Engine debug view
# ---------------------------------------------------------------------------


def test_engine_debug_reports_budget(db):
  chat_id = _create_chat_id(db)
  _insert_message(chat_id, "user", "hi")
  engine = ContextEngine(model_context_window=4096, reserved_output_tokens=1024)
  package = engine.build(chat_id=chat_id, user_message="hi", base_prompt="sys", instructions="instr")
  debug = package.debug()
  assert debug["budget"]["window"] == 4096
  assert debug["sections_tokens"]["system"] >= 1


def test_debug_context_shows_retrieved_memories(db):
  from services.context.engine import debug_context

  from services.memory import MemoryService

  MemoryService.create("fact", "User prefers React Native CLI.", importance=3)
  chat = _make_chat(db)
  chat.send("Which CLI does the user prefer?")

  info = debug_context(chat.id).debug()
  assert info["chat_id"] == chat.id
  assert any("React Native" in memory["content"] for memory in info["memories"])
  assert info["sections_tokens"]["system"] > 0


# ---------------------------------------------------------------------------
# Helpers (module-level so fixtures can't bleed between tests)
# ---------------------------------------------------------------------------


def _create_chat_id(db) -> int:
  session = get_session()
  try:
    record = ChatRecord(title=None)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record.id
  finally:
    session.close()


def _insert_message(chat_id: int, role: str, content: str) -> None:
  from models.message import Message

  session = get_session()
  try:
    session.add(Message(chat_id=chat_id, role=role, content=content))
    session.commit()
  finally:
    session.close()