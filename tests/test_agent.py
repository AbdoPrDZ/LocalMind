import json

from pydantic import BaseModel

from utils.agent import Agent
from utils.tool import Tool


class DoThingInput(BaseModel):
  value: str


class DoThingTool(Tool):
  name = "do_thing"
  description = "does a thing"
  input_model = DoThingInput

  def execute(self, arguments):
    return {"did": arguments["value"]}


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
  return Agent(tools=[DoThingTool()], system_prompt="system")


# ---------------------------------------------------------------------------
# Non-streaming tool loop
# ---------------------------------------------------------------------------


def _tool_round(messages, stream):
  assert stream is False
  return {
    "choices": [{
      "message": {
        "role": "assistant",
        "content": "calling tool",
        "tool_calls": [{
          "id": "call_1",
          "type": "function",
          "function": {"name": "do_thing", "arguments": json.dumps({"value": "x"})},
        }],
      }
    }]
  }


def _final_round(messages, stream):
  assert stream is False
  # The executed tool result must carry name + tool_call_id for remote providers.
  last = messages[-1]
  assert last["role"] == "tool"
  assert last["name"] == "do_thing"
  assert last["tool_call_id"] == "call_1"
  return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}


def test_agent_run_tool_loop(monkeypatch):
  monkeypatch.setattr("utils.agent.get_llm", lambda: StubProvider([_tool_round, _final_round]))

  agent = _make_agent()
  answer = agent.run([{"role": "user", "content": "go"}])

  assert answer == "done"


def test_agent_run_unknown_tool(monkeypatch):
  def _unknown_round(messages, stream):
    assert stream is False
    return {
      "choices": [{
        "message": {
          "role": "assistant",
          "content": "",
          "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "nope", "arguments": "{}"},
          }],
        }
      }]
    }

  def _after_unknown(messages, stream):
    last = messages[-1]
    assert last["role"] == "tool"
    assert json.loads(last["content"]) == {"error": "Unknown tool: nope"}
    return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

  monkeypatch.setattr("utils.agent.get_llm", lambda: StubProvider([_unknown_round, _after_unknown]))

  agent = _make_agent()
  assert agent.run([{"role": "user", "content": "go"}]) == "ok"


def test_agent_records_tool_results(monkeypatch):
  monkeypatch.setattr("utils.agent.get_llm", lambda: StubProvider([_tool_round, _final_round]))

  agent = _make_agent()
  agent.run([{"role": "user", "content": "go"}])

  results = agent.take_tool_results()
  assert results == [{"name": "do_thing", "result": {"did": "x"}}]
  # Consumed once; nothing left over for the next call.
  assert agent.take_tool_results() == []


def test_agent_skips_error_tool_results(monkeypatch):
  def _bad_round(messages, stream):
    return {
      "choices": [{
        "message": {
          "role": "assistant",
          "content": "",
          "tool_calls": [{
            "id": "c",
            "type": "function",
            "function": {"name": "do_thing", "arguments": json.dumps({"value": "x"})},
          }],
        }
      }]
    }

  def _after_bad(messages, stream):
    return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

  class _Exploding(DoThingTool):
    def execute(self, arguments):
      return {"error": "nope"}

  monkeypatch.setattr("utils.agent.get_llm", lambda: StubProvider([_bad_round, _after_bad]))
  agent = Agent(tools=[_Exploding()], system_prompt="system")

  agent.run([{"role": "user", "content": "go"}])
  assert agent.take_tool_results() == []


# ---------------------------------------------------------------------------
# clean_answer: thinking/reasoning leaks
# ---------------------------------------------------------------------------


def test_clean_answer_strips_thinking_tags():
  from utils.agent import clean_answer

  raw = "<thinking>\nHmm, let me parse this.\n</thinking>\nThe answer is 42."
  assert clean_answer(raw) == "The answer is 42."


def test_clean_answer_strips_qwen_template_think_answer():
  from utils.agent import clean_answer

  raw = (
    "<|im_start|>think\nThe user's last message is 'hi'.\n<|im_start|>answer\n"
    "Hello there."
  )
  assert clean_answer(raw) == "Hello there."


def test_clean_answer_strips_bare_thinking_response_preamble():
  from utils.agent import clean_answer

  raw = " thinking\nThe user is asking about context.\nresponse\nThe chat accumulates."
  assert clean_answer(raw) == "The chat accumulates."


def test_clean_answer_leaves_plain_answers_alone():
  from utils.agent import clean_answer

  assert clean_answer("Nothing fancy here.") == "Nothing fancy here."


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def test_agent_run_stream_verbatim(monkeypatch):
  def _text_stream(messages, stream):
    assert stream is True
    yield {"choices": [{"delta": {"content": "Hel"}}]}
    yield {"choices": [{"delta": {"content": "lo"}}]}

  stub = StubProvider([_text_stream])
  stub.stream_marker = None
  monkeypatch.setattr("utils.agent.get_llm", lambda: stub)

  agent = _make_agent()
  assert "".join(agent.run_stream([{"role": "user", "content": "hi"}])) == "Hello"


def test_agent_run_stream_hides_qwen_preamble(monkeypatch):
  def _qwen_stream(messages, stream):
    assert stream is True
    yield {"choices": [{"delta": {"content": " thinking quiet steps\n"}}]}
    yield {"choices": [{"delta": {"content": "response\nAnswer text"}}]}

  stub = StubProvider([_qwen_stream])
  stub.stream_marker = "response"
  monkeypatch.setattr("utils.agent.get_llm", lambda: stub)

  agent = _make_agent()
  assert "".join(agent.run_stream([{"role": "user", "content": "hi"}])) == "Answer text"


def test_agent_run_stream_tool_loop(monkeypatch):
  def _tool_call_stream(messages, stream):
    assert stream is True
    yield {
      "choices": [{
        "delta": {
          "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "do_thing", "arguments": json.dumps({"value": "s"})},
          }]
        }
      }]
    }

  def _final_stream(messages, stream):
    assert stream is True
    # Tool-call round must be preserved on the assistant message so remote
    # providers can pair results, and the tool result must carry name + id.
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["name"] == "do_thing"
    assert messages[-1]["tool_call_id"] == "call_1"
    assistant = messages[-2]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"]["name"] == "do_thing"
    yield {"choices": [{"delta": {"content": "the answer"}}]}

  stub = StubProvider([_tool_call_stream, _final_stream])
  stub.stream_marker = None
  monkeypatch.setattr("utils.agent.get_llm", lambda: stub)

  agent = _make_agent()
  assert "".join(agent.run_stream([{"role": "user", "content": "go"}])) == "the answer"