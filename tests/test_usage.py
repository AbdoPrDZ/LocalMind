import pytest
from pydantic import BaseModel

from models.chat import Chat as ChatRecord
from services.usage import UsageService, estimate_cost, model_base
from database import get_session
from utils.agent import Agent
from utils.tool import Tool


class StubInput(BaseModel):
  value: str


class StubTool(Tool):
  name = "stub"
  description = "stub tool"
  input_model = StubInput

  def execute(self, arguments):
    return {"ok": True}


class StubProvider:
  stream_marker = None

  def __init__(self, rounds):
    self.rounds = list(rounds)
    self.calls = 0

  def create_chat_completion(self, messages, tools=None, max_tokens=1024, stream=False):
    payload = self.rounds[self.calls]
    self.calls += 1
    return payload(messages, stream)


def _make_agent() -> Agent:
  return Agent(tools=[StubTool()], system_prompt="system")


# ---------------------------------------------------------------------------
# estimate_cost / model_base
# ---------------------------------------------------------------------------


def test_model_base_strips_suffixes():
  assert model_base("gemini-3.5-flash-latest") == "gemini-3.5-flash"
  assert model_base("gemini-3.5-flash-preview") == "gemini-3.5-flash"
  assert model_base("Gemini-2.5-Flash") == "gemini-2.5-flash"


def test_estimate_cost_gemini():
  assert estimate_cost("gemini", "gemini-3.5-flash", 1_000_000, 0) == pytest.approx(1.50)
  assert estimate_cost("gemini", "gemini-3.5-flash", 0, 1_000_000) == pytest.approx(9.00)
  assert estimate_cost("gemini", "gemini-3.5-flash", 1_000, 2_000) == pytest.approx(
    0.0195
  )


def test_estimate_cost_suffix_and_lite():
  assert estimate_cost("gemini", "gemini-3.5-flash-lite-latest", 1_000_000, 0) == pytest.approx(0.75)
  assert estimate_cost("gemini", "gemini-3.5-flash-lite", 0, 1_000_000) == pytest.approx(4.50)


def test_estimate_cost_unknown_model_uses_fallback():
  assert estimate_cost("gemini", "some-future-model", 1_000_000, 0) == pytest.approx(2.50)


def test_estimate_cost_local_is_free():
  assert estimate_cost("local", "qwen3-4b-instruct-gguf", 10_000, 10_000) == 0.0


def test_estimate_cost_gemini_model_via_openai_provider_is_billed():
  assert estimate_cost("openai", "gemini-3.5-flash", 1_000_000, 0) == pytest.approx(1.50)
  assert estimate_cost("openai", "openrouter/free", 1_000_000, 0) == 0.0
  assert estimate_cost("openai", "gemini-2.5-flash", 1_000_000, 0) == pytest.approx(0.30)


# ---------------------------------------------------------------------------
# Session lifecycle and aggregations
# ---------------------------------------------------------------------------


def _seed_chat(provider: str, model: str) -> int:
  session = get_session()
  try:
    record = ChatRecord(title=f"chat-{model}")
    session.add(record)
    session.commit()
    session.refresh(record)
    chat_id = record.id
  finally:
    session.close()
  return chat_id


def test_session_lifecycle(db):
  chat_id = _seed_chat("gemini", "gemini-3.5-flash")
  session_id = UsageService.start_session(chat_id, "gemini", "gemini-3.5-flash")

  opened = UsageService.get_session(session_id)
  assert opened is not None
  assert opened["chat_id"] == chat_id
  assert opened["provider"] == "gemini"
  assert opened["model"] == "gemini-3.5-flash"
  assert opened["started_at"] is not None
  assert opened["ended_at"] is None
  assert opened["total_tokens"] == 0
  assert opened["cost"] == 0.0

  UsageService.record(session_id, 1000, 500)
  used = UsageService.get_session(session_id)
  assert used["prompt_tokens"] == 1000
  assert used["completion_tokens"] == 500
  assert used["total_tokens"] == 1500
  assert used["cost"] == pytest.approx(estimate_cost("gemini", "gemini-3.5-flash", 1000, 500))

  UsageService.close_session(session_id)
  closed = UsageService.get_session(session_id)
  assert closed["ended_at"] is not None

  # Close is idempotent and totals survive.
  UsageService.close_session(session_id)
  totals = UsageService.totals_for_chat(chat_id)
  assert totals["total_tokens"] == 1500
  assert totals["sessions"] == 1


def test_aggregations_global_by_chat_by_model(db):
  chat_a = _seed_chat("gemini", "gemini-3.5-flash")
  chat_b = _seed_chat("gemini", "gemini-3.5-flash-lite")
  chat_c = _seed_chat("local", "qwen3-4b-instruct-gguf")

  sa = UsageService.start_session(chat_a, "gemini", "gemini-3.5-flash")
  sb1 = UsageService.start_session(chat_b, "gemini", "gemini-3.5-flash-lite")
  sb2 = UsageService.start_session(chat_b, "gemini", "gemini-3.5-flash-lite")
  sc = UsageService.start_session(chat_c, "local", "qwen3-4b-instruct-gguf")

  UsageService.record(sa, 1_000_000, 0)
  UsageService.record(sb1, 0, 1_000_000)
  UsageService.record(sb2, 1_000_000, 0)
  UsageService.record(sc, 500_000, 500_000)

  totals = UsageService.totals()
  assert totals["sessions"] == 4
  assert totals["prompt_tokens"] == 2_500_000
  assert totals["completion_tokens"] == 1_500_000
  assert totals["total_tokens"] == 4_000_000
  assert totals["cost"] == pytest.approx(1.5 + 4.5 + 0.75 + 0.0)

  by_chat = {row["chat_id"]: row for row in UsageService.totals_by_chat()}
  assert by_chat[chat_a]["total_tokens"] == 1_000_000
  assert by_chat[chat_b]["total_tokens"] == 2_000_000
  assert by_chat[chat_b]["sessions"] == 2
  assert by_chat[chat_c]["cost"] == 0.0
  # Most expensive first.
  assert by_chat and list(by_chat.values())[0]["chat_id"] == chat_b

  by_model = {row["model"]: row for row in UsageService.totals_by_model()}
  assert by_model["gemini-3.5-flash"]["total_tokens"] == 1_000_000
  assert by_model["gemini-3.5-flash-lite"]["total_tokens"] == 2_000_000
  assert by_model["gemini-3.5-flash-lite"]["provider"] == "gemini"
  assert by_model["qwen3-4b-instruct-gguf"]["cost"] == 0.0


def test_aggregations_empty(db):
  totals = UsageService.totals()
  assert totals["sessions"] == 0
  assert totals["total_tokens"] == 0
  assert totals["cost"] == 0.0
  assert UsageService.totals_by_chat() == []
  assert UsageService.totals_by_model() == []


# ---------------------------------------------------------------------------
# Agent usage accumulation
# ---------------------------------------------------------------------------


def test_agent_run_accumulates_usage(monkeypatch):
  def _final_round(messages, stream):
    assert stream is False
    return {
      "choices": [{"message": {"role": "assistant", "content": "done"}}],
      "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }

  monkeypatch.setattr("utils.agent.get_llm", lambda: StubProvider([_final_round]))

  agent = _make_agent()
  assert agent.run([{"role": "user", "content": "go"}]) == "done"

  usage = agent.take_usage()
  assert usage == {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
  assert agent.take_usage() == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_agent_run_stream_accumulates_usage_chunks(monkeypatch):
  def _stream(messages, stream):
    assert stream is True
    yield {"choices": [{"delta": {"content": "Hel"}}]}
    yield {"usage": {"prompt_tokens": 30, "completion_tokens": 4, "total_tokens": 34}}
    yield {"choices": [{"delta": {"content": "lo"}}]}
    yield {"usage": {"prompt_tokens": 30, "completion_tokens": 4, "total_tokens": 34}}

  stub = StubProvider([_stream])
  stub.stream_marker = None
  monkeypatch.setattr("utils.agent.get_llm", lambda: stub)

  agent = _make_agent()
  assert "".join(agent.run_stream([{"role": "user", "content": "hi"}])) == "Hello"

  usage = agent.take_usage()
  assert usage == {"prompt_tokens": 60, "completion_tokens": 8, "total_tokens": 68}


# ---------------------------------------------------------------------------
# Chat wiring: open on create, record on send, close on exit
# ---------------------------------------------------------------------------


class StubAgent:
  system_prompt = "sys"

  def __init__(self):
    self.calls = 0

  def run(self, messages):
    self.calls += 1
    return "hello"

  def run_stream(self, messages):
    self.calls += 1
    yield "hi "
    yield "there"

  def take_usage(self):
    return {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_chat_opens_sends_records_and_closes(db, monkeypatch):
  from apps.base import Chat

  monkeypatch.setattr(
    "apps.base._build_agent",
    lambda *a, **k: StubAgent(),
  )

  chat = Chat.create()
  session_id = chat.usage_session_id
  assert session_id is not None

  answer = chat.send("hi")
  assert answer == "hello"

  used = UsageService.get_session(session_id)
  assert used["prompt_tokens"] == 10
  assert used["completion_tokens"] == 5
  assert used["total_tokens"] == 15
  assert used["ended_at"] is None
  assert used["cost"] == pytest.approx(estimate_cost(used["provider"], used["model"], 10, 5))

  summary = chat.usage_summary()
  assert summary["session"]["total_tokens"] == 15
  assert summary["chat_totals"]["sessions"] == 1
  assert summary["global_totals"]["sessions"] >= 1

  chat.close()
  assert chat.usage_session_id is None
  assert UsageService.get_session(session_id)["ended_at"] is not None


def test_chat_stream_records_usage_on_completion(db, monkeypatch):
  from apps.base import Chat

  monkeypatch.setattr(
    "apps.base._build_agent",
    lambda *a, **k: StubAgent(),
  )

  chat = Chat.create()
  session_id = chat.usage_session_id

  reply = "".join(chat.send_stream("hi"))
  assert reply == "hi there"
  assert chat.history == [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hi there"},
  ]

  used = UsageService.get_session(session_id)
  assert used["total_tokens"] == 15

  chat.close()
  assert UsageService.get_session(session_id)["ended_at"] is not None


def test_chat_load_resumes_with_new_session(db, monkeypatch):
  from apps.base import Chat

  monkeypatch.setattr(
    "apps.base._build_agent",
    lambda *a, **k: StubAgent(),
  )

  first = Chat.create()
  first_id = first.id
  first_session = first.usage_session_id
  first.send("hi")
  first.close()

  resumed = Chat.load(first_id)
  assert resumed.id == first_id
  assert resumed.usage_session_id != first_session
  assert resumed.usage_session_id is not None
  resumed.send("again")
  resumed.close()

  totals = UsageService.totals_for_chat(first_id)
  assert totals["sessions"] == 2
  assert totals["total_tokens"] == 30


# ---------------------------------------------------------------------------
# Chat title generation (on the first real exchange)
# ---------------------------------------------------------------------------


class _TitleStubAgent:
  tools = []
  system_prompt = ""

  def run(self, messages):
    return "ok"

  def take_usage(self):
    return {}


def test_send_sets_title_from_llm(monkeypatch, db):
  from apps.base import Chat

  monkeypatch.setattr(
    "apps.base._build_agent",
    lambda *a, **k: _TitleStubAgent(),
  )
  monkeypatch.setattr(
    "apps.base._generated_title",
    lambda msg: ("My Title", {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}),
  )

  chat = Chat.create()
  assert chat.title is None
  chat.send("hello there")

  assert chat.title == "My Title"
  # The title call's tokens are recorded against the same usage session.
  assert chat.usage_summary()["session"]["prompt_tokens"] == 5
  assert chat.usage_summary()["session"]["completion_tokens"] == 2


def test_send_falls_back_to_first_message(monkeypatch, db):
  from apps.base import Chat

  monkeypatch.setattr(
    "apps.base._build_agent",
    lambda *a, **k: _TitleStubAgent(),
  )

  chat = Chat.create()
  chat.send("what is 2+2?")

  assert chat.title == "what is 2+2?"


def test_title_not_regenerated(monkeypatch, db):
  from apps.base import Chat

  call_count = 0

  def gen(msg):
    nonlocal call_count
    call_count += 1
    return ("T", None)

  monkeypatch.setattr("apps.base._build_agent", lambda *a, **k: _TitleStubAgent())
  monkeypatch.setattr("apps.base._generated_title", gen)

  chat = Chat.create()
  chat.send("first")
  assert chat.title == "T"

  chat.send("second")
  assert call_count == 1