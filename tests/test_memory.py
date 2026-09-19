import pytest
from pydantic import ValidationError

from database import get_session
from models.chat import Chat
from models.message import Message
from services.global_context import build_global_context
from services.memory import MemoryService
from tools.memory import build_memory_tools


def _make_chat(context=None) -> int:
  session = get_session()
  try:
    chat = Chat(title="test")
    chat.context = context
    session.add(chat)
    session.commit()
    session.refresh(chat)
    return chat.id
  finally:
    session.close()


def _add_message(chat_id: int, role: str, content: str) -> int:
  session = get_session()
  try:
    message = Message(chat_id=chat_id, role=role, content=content)
    session.add(message)
    session.commit()
    session.refresh(message)
    return message.id
  finally:
    session.close()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_create_get_update_delete(db):
  memory = MemoryService.create(
    "fact", "EMoveX uses Odoo as its backend.", importance=3
  )
  assert memory["id"] > 0
  assert memory["type"] == "fact"
  assert memory["source_chat_id"] is None

  loaded = MemoryService.get(memory["id"])
  assert loaded["content"] == "EMoveX uses Odoo as its backend."

  updated = MemoryService.update(memory["id"], content="EMoveX uses Odoo ERP.")
  assert updated["content"] == "EMoveX uses Odoo ERP."

  assert MemoryService.delete(memory["id"]) is True
  assert MemoryService.get(memory["id"]) is None
  assert MemoryService.delete(memory["id"]) is False


def test_create_validates_type_and_importance(db):
  with pytest.raises(ValueError):
    MemoryService.create("bogus", "content")
  with pytest.raises(ValueError):
    MemoryService.create("fact", "content", importance=9)
  with pytest.raises(ValueError):
    MemoryService.update(1, type="bogus")


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def test_duplicate_guard(db):
  first = MemoryService.create(
    "preference", "User prefers React Native CLI instead of Expo.", importance=3
  )
  duplicate = MemoryService.create(
    "preference", "  user prefers   react NATIVE cli instead of expo.  "
  )
  assert duplicate["duplicate"] is True
  assert duplicate["memory"]["id"] == first["id"]


# ---------------------------------------------------------------------------
# Search & ranking
# ---------------------------------------------------------------------------


def test_search_ranks_by_importance(db):
  MemoryService.create("topic", "Favorite color is blue.", importance=1)
  MemoryService.create(
    "decision", "Use FCM for background notifications.", importance=4
  )
  MemoryService.create("preference", "Prefers Expo Router for navigation.", importance=2)

  results = MemoryService.search("react native")
  assert results == []

  results = MemoryService.search("notification")
  assert len(results) == 1
  assert results[0]["content"].startswith("Use FCM")

  results = MemoryService.search("prefer", limit=10)
  assert any("FCM" in result["content"] for result in results) is False
  ranked = sorted(results, key=lambda m: m["importance"], reverse=True)
  assert [r["id"] for r in ranked] == [r["id"] for r in results]


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_provenance_source_chat_id(db):
  chat_id = _make_chat()
  memory = MemoryService.create(
    "decision", "Use FCM in React Native.", source_chat_id=chat_id
  )
  assert MemoryService.get(memory["id"])["source_chat_id"] == chat_id


# ---------------------------------------------------------------------------
# Chat context & history retrieval
# ---------------------------------------------------------------------------


def test_get_chat_context(db):
  chat_id = _make_chat(context="We discussed FCM background notifications.")
  assert MemoryService.get_chat_context(chat_id) == "We discussed FCM background notifications."
  assert MemoryService.get_chat_context(999_999) is None


def test_search_chat_history(db):
  other = _make_chat()
  chat_id = _make_chat()
  _add_message(chat_id, "user", "How do I set up FCM background notifications?")
  _add_message(other, "user", "React Native uses EAS builds.")

  results = MemoryService.search_chat_history("FCM")
  assert len(results) == 1
  assert results[0]["chat_id"] == chat_id
  assert results[0]["role"] == "user"

  results = MemoryService.search_chat_history("React Native", limit=10)
  assert all(result["chat_id"] == other for result in results)


# ---------------------------------------------------------------------------
# Global context builder
# ---------------------------------------------------------------------------


def test_global_context_is_bounded_and_selective(db):
  _make_chat()
  for i in range(6):
    MemoryService.create("topic", f"Minor topic {i}.", importance=1)
  MemoryService.create(
    "decision", "The framework must stay generic.", importance=4
  )
  MemoryService.create(
    "preference", "User prefers CLI instead of Expo.", importance=3
  )

  block = build_global_context(max_entries=2, max_chars=4000)
  assert block.startswith("GLOBAL MEMORY")
  assert "The framework must stay generic." in block
  assert "User prefers CLI instead of Expo." in block
  assert "Important decisions" in block
  assert "Important preferences" in block
  assert len(block) <= 4000
  assert "Minor topic" not in block


def test_global_context_empty(db):
  assert build_global_context() == ""


def test_global_context_includes_current_chat_id(db):
  MemoryService.create("fact", "Something durable.", importance=3)
  block = build_global_context(current_chat_id=42)
  assert "CURRENT CHAT ID: 42" in block


# ---------------------------------------------------------------------------
# Memory tools
# ---------------------------------------------------------------------------


def _tool(name: str):
  return {tool.name: tool for tool in build_memory_tools()}[name]


def test_tool_registry_contains_memory_tools(db):
  names = {tool.name for tool in build_memory_tools()}
  assert names == {
    "search_global_memory",
    "get_memory",
    "get_chat_context",
    "search_chat_history",
    "save_memory",
    "forget_memory",
  }


def test_save_and_search_tools(db):
  result = _tool("save_memory").call(
    {
      "type": "decision",
      "content": "Use FCM for background notifications.",
      "importance": 3,
    }
  )
  assert result["success"] is True

  duplicate = _tool("save_memory").call(
    {
      "type": "decision",
      "content": "Use FCM for background notifications.",
      "importance": 2,
    }
  )
  assert "warning" in duplicate

  hits = _tool("search_global_memory").call({"query": "FCM"})
  assert len(hits) == 1
  assert hits[0]["importance"] == 3


def test_get_memory_tool(db):
  memory = MemoryService.create("fact", "Durable fact.", importance=4)
  assert _tool("get_memory").call({"memory_id": memory["id"]})["id"] == memory["id"]
  missing = _tool("get_memory").call({"memory_id": 999_999})
  assert missing["ok"] is False
  assert missing["error"]["code"] == "not_found"


def test_get_chat_context_tool(db):
  chat_id = _make_chat(context="We discussed FCM.")
  result = _tool("get_chat_context").call({"chat_id": chat_id})
  assert result == {"chat_id": chat_id, "context": "We discussed FCM."}
  missing = _tool("get_chat_context").call({"chat_id": 999_999})
  assert missing["ok"] is False
  assert missing["error"]["code"] == "not_found"


def test_search_chat_history_tool(db):
  chat_id = _make_chat()
  _add_message(chat_id, "user", "Set up FCM background handling.")
  results = _tool("search_chat_history").call({"query": "FCM", "chat_id": chat_id})
  assert len(results) == 1
  assert results[0]["message_id"] > 0


def test_tools_validate_arguments(db):
  with pytest.raises(ValidationError):
    _tool("search_global_memory").call({})
  with pytest.raises(ValidationError):
    _tool("get_memory").call({"memory_id": "abc"})
  with pytest.raises(ValidationError):
    _tool("save_memory").call({"type": "bogus", "content": "x"})


# ---------------------------------------------------------------------------
# Phase 3: FTS5 hybrid retrieval
# ---------------------------------------------------------------------------


def test_fts_token_match_finds_scattered_words(db):
  from database import is_fts_available

  if not is_fts_available():
    pytest.skip("FTS5 not available in this SQLite build")

  MemoryService.create("fact", "The weather forecast API is OpenWeather.", importance=4)
  MemoryService.create("fact", "OpenWeather offers a free tier.", importance=1)

  # "OpenWeather free tier" is not a substring of any entry — FTS token match
  # still retrieves the low-importance entry that hits all three words.
  results = MemoryService.search("OpenWeather free tier")
  assert len(results) == 1
  assert results[0]["content"].startswith("OpenWeather offers")


def test_fts_fallback_without_index(db, monkeypatch):
  import database as database_mod

  monkeypatch.setattr(database_mod, "_FTS_AVAILABLE", False)

  MemoryService.create("fact", "OpenWeather offers a free tier.", importance=1)
  MemoryService.create("fact", "The weather forecast API is OpenWeather.", importance=4)
  results = MemoryService.search("free tier")
  assert len(results) == 1
  assert results[0]["content"].startswith("OpenWeather offers")


# ---------------------------------------------------------------------------
# Auto-capture: durable tool findings become facts; transient web data does not
# ---------------------------------------------------------------------------

class _CaptureStubAgent:
  system_prompt = "sys"

  def __init__(self, results):
    self._results = results

  def run(self, messages):
    return "ok"

  def take_tool_results(self):
    return self._results

  def take_usage(self):
    return {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


def test_web_search_and_fetch_never_auto_captured(db, monkeypatch):
  from apps.base import Chat

  results = [
    {
      "name": "web_search",
      "result": {
        "query": "price of X",
        "results": [{"title": "X", "url": "http://example.test", "snippet": "the price"}],
      },
    },
    {"name": "fetch_page", "result": {"url": "http://example.test", "text": "X costs 5."}},
    {"name": "ask_user", "result": {"answer": "React Native"}},
  ]
  monkeypatch.setattr(
    "apps.base._build_agent",
    lambda *a, **k: _CaptureStubAgent(results),
  )

  chat = Chat.create()
  chat.send("Search the price of X.")
  chat.close()

  contents = [memory["content"] for memory in MemoryService.list()]
  assert any('The user answered "React Native"' in content for content in contents)
  assert not any("Web search" in content or "Fetched page" in content for content in contents)


# ---------------------------------------------------------------------------
# Phase 2: supersession, status & forgetting
# ---------------------------------------------------------------------------


def test_supersedes_same_subject_contradiction(db):
  old = MemoryService.create("fact", "LocalMind uses SQLite.", importance=2)
  MemoryService.create("fact", "LocalMind uses PostgreSQL.", importance=2)

  stale = MemoryService.get(old["id"])
  assert stale["status"] == "superseded"
  assert stale["superseded_by"] == old["id"] + 1

  active = MemoryService.list(limit=50)
  assert all(item["status"] == "active" for item in active)
  assert all(item["content"] != "LocalMind uses SQLite." for item in active)


def test_supersedes_flutter_to_react_native_preference(db):
  # Review T2: the same subject (mobile development) with a changed value.
  flutter = MemoryService.create(
    "preference", "User prefers Flutter for mobile development.", importance=3
  )
  react = MemoryService.create(
    "preference", "User prefers React Native for mobile development.", importance=3
  )
  assert MemoryService.get(flutter["id"])["status"] == "superseded"
  assert MemoryService.get(flutter["id"])["superseded_by"] == react["id"]
  assert MemoryService.get(react["id"])["status"] == "active"


def test_no_supersede_without_shared_subject(db):
  # Only boilеrplate "User prefers X." is shared — different subjects stay active.
  flutter = MemoryService.create("preference", "User prefers Flutter.", importance=3)
  vim = MemoryService.create("preference", "User prefers Vim.", importance=3)
  assert MemoryService.get(flutter["id"])["status"] == "active"
  assert MemoryService.get(vim["id"])["status"] == "active"


def test_no_supersede_when_subjects_differ(db):
  MemoryService.create("fact", "LocalMind uses SQLite.", importance=2)
  MemoryService.create("preference", "User prefers Flutter.", importance=3)

  active = MemoryService.list(limit=50)
  assert len(active) == 2
  assert all(item["status"] == "active" for item in active)


def test_forget_archives_memory(db):
  created = MemoryService.create("fact", "EMoveX backend runs Node 18.")
  assert MemoryService.search("Node") != []
  assert MemoryService.forget(created["id"]) is True
  assert MemoryService.search("Node") == []
  assert [m["id"] for m in MemoryService.list(status="archived")] == [created["id"]]
  assert MemoryService.forget(999_999) is False


def test_forget_by_query(db):
  MemoryService.create("fact", "Flutter requires the Dart SDK.", importance=2)
  MemoryService.create("topic", "Flutter is my favorite app to build.", importance=2)
  MemoryService.create("fact", "LocalMind is an offline LLM platform.", importance=2)

  archived = MemoryService.forget_by_query("flutter")
  assert len(archived) == 2
  remaining = MemoryService.list(limit=50)
  assert len(remaining) == 1
  assert remaining[0]["content"] == "LocalMind is an offline LLM platform."


def test_forget_memory_tool(db):
  memory = MemoryService.create("fact", "EMoveX uses Odoo.", importance=3)
  assert _tool("forget_memory").call({"memory_id": memory["id"]})["success"] is True
  assert MemoryService.search("Odoo") == []
  missing = _tool("forget_memory").call({"memory_id": 999_999})
  assert missing["ok"] is False
  assert missing["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# Phase 2: usage stats & keyword ranking
# ---------------------------------------------------------------------------


def test_search_tracks_access_stats(db):
  created = MemoryService.create("fact", "LocalMind runs fully offline.")
  assert created["access_count"] == 0
  MemoryService.search("offline")
  MemoryService.search("offline")
  again = MemoryService.get(created["id"])
  assert again["access_count"] == 2
  assert again["last_accessed_at"] is not None


def test_search_ranks_keyword_match_first(db):
  MemoryService.create("fact", "The weather forecast API is OpenWeather.", importance=4)
  MemoryService.create("fact", "OpenWeather offers a free tier.", importance=1)

  # "free tier" only occurs in the low-importance entry; substring+keyword still
  # finds it (keyword overlap, not importance, drives ranking here).
  results = MemoryService.search("free tier")
  assert len(results) == 1
  assert results[0]["content"].startswith("OpenWeather offers")


# ---------------------------------------------------------------------------
# Phase 5: conflict & staleness inspection
# ---------------------------------------------------------------------------


def test_conflicts_detects_same_subject_active_pairs(db):
  MemoryService.create("fact", "LocalMind persists chats.", importance=2)
  # A later edit (not a fresh create) introduces the contradiction — updates do
  # not run auto-supersession, so both remain active and surface as a conflict.
  other = MemoryService.create("fact", "EMoveX uses Odoo.", importance=2)
  MemoryService.update(other["id"], content="LocalMind stores chats in memory only.")

  pairs = MemoryService.conflicts()
  assert len(pairs) == 1
  assert pairs[0]["overlap"] >= 0.5
  assert set(pairs[0]["shared_subject"]) == {"localmind", "chats"}


def test_conflicts_empty_without_overlap(db):
  MemoryService.create("fact", "LocalMind persists chats.", importance=2)
  MemoryService.create("fact", "EMoveX uses Odoo.", importance=2)
  assert MemoryService.conflicts() == []


def test_stale_memories(db):
  from datetime import timedelta

  from database import get_session as raw_session
  from models.memory import Memory
  from utils.time import utcnow

  fresh = MemoryService.create("fact", "Fresh fact about Odoo.", importance=1)
  old = MemoryService.create("fact", "Old fact about Odoo.", importance=1)

  session = raw_session()
  row = session.get(Memory, old["id"])
  row.updated_at = utcnow() - timedelta(days=45)
  row.access_count = 0
  session.commit()
  session.close()

  stale = MemoryService.stale(days=30)
  assert [m["id"] for m in stale] == [old["id"]]
  assert all(m["id"] != fresh["id"] for m in stale)
  assert MemoryService.stale(days=90) == []