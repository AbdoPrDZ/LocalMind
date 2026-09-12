"""End-to-end persistence: tool data survives across turns and chats.

Drives a real ``Chat`` service with a fake LLM + fake web tool to prove the
two retention guarantees:
- tool results are folded into the chat context when the model emits no
  ``<context>`` block (in-chat memory);
- tool findings are captured into global memory (cross-chat memory).
"""

import json

from pydantic import BaseModel

from database import get_session
from models.chat import Chat as ChatRecord
from services.memory import MemoryService
from utils.agent import Agent
from utils.tool import Tool


class _FetchInput(BaseModel):
  url: str


class _FakeFetchTool(Tool):
  name = "fetch_page"
  description = "fetch a page"
  input_model = _FetchInput

  def execute(self, arguments):
    return {
      "url": arguments["url"],
      "text": (
        "AbdoPrDZ — Abderrahmane GUERGUER, Algeria-Ghardaia-Berriane, "
        "74 repositories, 13 stars."
      ),
    }


class _FakeLLM:
  stream_marker = None

  def __init__(self, rounds):
    self.rounds = list(rounds)
    self.calls = 0

  def create_chat_completion(self, messages, tools=None, max_tokens=1024, stream=False):
    if tools is None:  # the title-generating call needs no tools
      return {"choices": [{"message": {"role": "assistant", "content": "Profile chat"}}], "usage": {}}
    payload = self.rounds[self.calls]
    self.calls += 1
    return payload(messages, stream)


def _tool_round(messages, stream):
  return {
    "choices": [{"message": {
      "role": "assistant",
      "content": "",
      "tool_calls": [{
        "id": "c1",
        "type": "function",
        "function": {
          "name": "fetch_page",
          "arguments": json.dumps({"url": "https://github.com/AbdoPrDZ"}),
        },
      }],
    }}],
    "usage": {},
  }


def _plain_round(text):
  def _round(messages, stream):
    return {"choices": [{"message": {"role": "assistant", "content": text}}], "usage": {}}
  return _round


def _make_chat(db) -> "Chat":
  from apps.base import Chat

  session = get_session()
  record = ChatRecord(title=None)
  session.add(record)
  session.commit()
  session.refresh(record)
  session.expunge(record)
  session.close()
  agent = Agent(tools=[_FakeFetchTool()], system_prompt="system")
  return Chat(record, agent, usage_session_id=None)


def test_tool_data_survives_within_chat_without_model_context(db, monkeypatch):
  monkeypatch.setattr("utils.agent.get_llm", lambda: _FakeLLM([_tool_round, _plain_round("Here is your profile summary.")]))

  chat = _make_chat(db)
  answer = chat.send("review my github profile")

  assert answer == "Here is your profile summary."
  assert len(chat.history) == 2
  assert chat.context is not None
  assert "RECENT TOOL RESULTS" in chat.context
  assert "fetch_page" in chat.context
  assert "GUERGUER" in chat.context


def test_tool_data_carries_into_next_turn(db, monkeypatch):
  monkeypatch.setattr(
    "utils.agent.get_llm",
    lambda: _FakeLLM([
      _tool_round,
      _plain_round("Here is your profile summary."),
      _plain_round("Based on the profile, your location is Algeria-Ghardaia-Berriane."),
    ]),
  )

  chat = _make_chat(db)
  chat.send("review my github profile")
  # Turn 2 runs no tools and emits no <context> block — the previous context
  # must still be there, not replaced by the new topic.
  monkeypatch.setattr(
    "utils.agent.get_llm",
    lambda: _FakeLLM([_plain_round("Based on the profile, your address is Algeria-Ghardaia-Berriane.")]),
  )
  second = chat.send("so what is my address?")

  assert "address" in second.lower()
  assert chat.context.count("RECENT TOOL RESULTS") == 1
  assert "GUERGUER" in chat.context


def test_tool_facts_are_captured_into_global_memory(db, monkeypatch):
  monkeypatch.setattr("utils.agent.get_llm", lambda: _FakeLLM([_tool_round, _plain_round("ok")]))

  chat = _make_chat(db)
  chat.send("look at my github profile")

  memories = MemoryService.list(limit=20)
  captured = [m for m in memories if m["content"].startswith("Fetched page https://github.com/AbdoPrDZ:")]
  assert captured, f"no auto-captured memory, got {memories}"
  assert "GUERGUER" in captured[0]["content"]
  assert captured[0]["source_chat_id"] == chat.id
  assert captured[0]["importance"] == 1


def test_auto_capture_can_be_disabled(db, monkeypatch):
  monkeypatch.setenv("AUTO_MEMORIZE", "0")
  monkeypatch.setattr("utils.agent.get_llm", lambda: _FakeLLM([_tool_round, _plain_round("ok")]))

  before = MemoryService.list(limit=50)
  chat = _make_chat(db)
  chat.send("look at my github profile")

  after = MemoryService.list(limit=50)
  assert len(after) == len(before)


# ---------------------------------------------------------------------------
# Surrogate sanitization: sqlite's UTF-8 driver rejects lone surrogates
# ---------------------------------------------------------------------------


def test_clean_text_removes_lone_surrogates_keeps_valid_unicode():
  from apps.base import _clean_text

  assert _clean_text("hi \udcff there") == "hi  there"
  assert _clean_text(None) is None
  # Valid multi-codepoint unicode (emoji) must survive untouched.
  assert _clean_text("heart \u2764\ufe0f ok") == "heart \u2764\ufe0f ok"


def test_send_sanitizes_surrogate_user_message(db, monkeypatch):
  monkeypatch.setattr("utils.agent.get_llm", lambda: _FakeLLM([_plain_round("ok")]))

  chat = _make_chat(db)
  answer = chat.send("hello \udcff world")

  assert answer == "ok"
  assert chat.history[0]["role"] == "user"
  assert "\udcff" not in chat.history[0]["content"]


def test_send_stream_sanitizes_surrogate_user_message(db, monkeypatch):
  def _text_stream(messages, stream):
    assert stream is True
    yield {"choices": [{"delta": {"content": "o"}}]}
    yield {"choices": [{"delta": {"content": "k"}}]}

  monkeypatch.setattr("utils.agent.get_llm", lambda: _FakeLLM([_text_stream]))

  chat = _make_chat(db)
  out = "".join(chat.send_stream("hello \udcff again"))

  assert out == "ok"
  assert chat.history[0]["role"] == "user"
  assert "\udcff" not in chat.history[0]["content"]


# ---------------------------------------------------------------------------
# Provider failures must not kill the cmd session
# ---------------------------------------------------------------------------


def test_cmd_survives_provider_error(db, monkeypatch, capsys):
  from apps.cmd.main import _print_reply

  def _boom(messages, stream):
    raise RuntimeError(
      "https://openrouter.ai/api/v1/chat/completions failed with HTTP 429: quota"
    )

  monkeypatch.setattr("utils.agent.get_llm", lambda: _FakeLLM([_boom]))

  chat = _make_chat(db)
  _print_reply(chat, "ask about history")

  out = capsys.readouterr().out
  assert "Error: " in out
  assert "HTTP 429" in out
  # The user message was still persisted before the provider failed.
  assert chat.history[0]["role"] == "user"
  assert chat.history[0]["content"] == "ask about history"