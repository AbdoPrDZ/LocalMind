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
  assert _tool("get_memory").call({"memory_id": 999_999}) == {"error": "Memory not found"}


def test_get_chat_context_tool(db):
  chat_id = _make_chat(context="We discussed FCM.")
  result = _tool("get_chat_context").call({"chat_id": chat_id})
  assert result == {"chat_id": chat_id, "context": "We discussed FCM."}
  assert _tool("get_chat_context").call({"chat_id": 999_999}) == {"error": "Chat not found"}


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