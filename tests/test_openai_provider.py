import json
import os

import pytest

os.environ["LLM_PROVIDER"] = "openai"
os.environ["LLM_OPENAI_API_KEY"] = "openrouter-test-key"
os.environ["LLM_GEMINI_API_KEY"] = "gemini-test-key"

from utils.providers.openai import (  # noqa: E402
  OpenAILLMProvider,
  _chat_url,
  _complete_tool_calls,
  _error_text,
  is_gemini_model,
  iter_stream_chunks,
  merge_delta_tool_calls,
)


def test_is_gemini_model():
  assert is_gemini_model("gemini-3.5-flash")
  assert is_gemini_model("gemini-3.5-flash-latest")
  assert is_gemini_model("Gemini-2.5-Flash")
  assert not is_gemini_model("openrouter/free")
  assert not is_gemini_model("nvidia/foo")


@pytest.fixture()
def provider():
  os.environ["LLM_OPENAI_API_KEY"] = "openrouter-test-key"
  os.environ["LLM_GEMINI_API_KEY"] = "gemini-test-key"
  return OpenAILLMProvider()


# ---------------------------------------------------------------------------
# Backend routing
# ---------------------------------------------------------------------------


def test_routes_free_model_to_router_by_default(provider):
  provider.model = "openrouter/free"
  base_url, api_key = provider._endpoint()

  assert base_url.endswith("openrouter.ai/api/v1")
  assert api_key == "openrouter-test-key"


def test_routes_gemini_model_to_gemini_endpoint(provider):
  provider.model = "gemini-3.5-flash"
  base_url, api_key = provider._endpoint()

  assert "generativelanguage.googleapis.com" in base_url
  assert "/openai/" in base_url
  assert api_key == "gemini-test-key"


def test_router_model_requires_key():
  from utils.env import ENV

  try:
    os.environ.pop("LLM_OPENAI_API_KEY", None)
    os.environ.pop("LLM_OPENROUTER_API_KEY", None)
    provider = OpenAILLMProvider()
    provider.model = "openrouter/free"
    with pytest.raises(ValueError, match="LLM_OPENAI_API_KEY"):
      provider._endpoint()
  finally:
    os.environ["LLM_OPENAI_API_KEY"] = "openrouter-test-key"


def test_gemini_model_via_openai_requires_key():
  try:
    os.environ.pop("LLM_GEMINI_API_KEY", None)
    provider = OpenAILLMProvider()
    provider.model = "gemini-3.5-flash"
    with pytest.raises(ValueError, match="LLM_GEMINI_API_KEY"):
      provider._endpoint()
  finally:
    os.environ["LLM_GEMINI_API_KEY"] = "gemini-test-key"


def test_payload(provider):
  payload = provider._payload(
    "model-x",
    [{"role": "user", "content": "hi"}],
    tools=[{"type": "function"}],
    max_tokens=128,
    stream=True,
  )

  assert payload["model"] == "model-x"
  assert payload["stream"] is True
  assert payload["max_tokens"] == 128
  assert payload["tools"] == [{"type": "function"}]


def test_payload_omits_tools_and_max_tokens_when_empty(provider):
  payload = OpenAILLMProvider._payload("m", [], None, 0, stream=False)
  assert "tools" not in payload
  assert "max_tokens" not in payload


# ---------------------------------------------------------------------------
# Streaming SSE parsing and tool-call merging
# ---------------------------------------------------------------------------


def test_iter_stream_chunks_emits_content_and_usage():
  lines = [
    "data: {\"choices\": [{\"delta\": {\"content\": \"Hel\"}}]}",
    "data: {\"choices\": [{\"delta\": {\"content\": \"lo\"}}]}",
    "data: {\"usage\": {\"prompt_tokens\": 10, \"completion_tokens\": 4, \"total_tokens\": 14}}",
    "data: [DONE]",
  ]

  chunks = list(iter_stream_chunks(lines))

  assert chunks == [
    {"choices": [{"delta": {"content": "Hel"}}]},
    {"choices": [{"delta": {"content": "lo"}}]},
    {"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}},
  ]


def test_iter_stream_chunks_ignores_keepalive_and_notice_lines():
  lines = [
    ": keep-alive comment",
    "data: [DONE]",
    "data: {\"foo\": 1}",
    "not a data line",
  ]
  assert list(iter_stream_chunks(lines)) == []


def test_iter_stream_chunks_merges_fragmented_tool_calls():
  # OpenRouter-style fragments, interleaved with text.
  lines = [
    "data: {\"choices\": [{\"delta\": {\"tool_calls\": [{\"index\": 0, \"id\": \"call_a\", \"function\": {\"name\": \"list_projects\", \"arguments\": \"{\\\"limit\\\":\"}}]}}]}",
    "data: {\"choices\": [{\"delta\": {\"content\": \"thinking\"}}]}",
    "data: {\"choices\": [{\"delta\": {\"tool_calls\": [{\"index\": 0, \"function\": {\"arguments\": \" 2}\"}}]}}]}",
    "data: [DONE]",
  ]

  chunks = list(iter_stream_chunks(lines))

  assert chunks == [
    {"choices": [{"delta": {"content": "thinking"}}]},
    {
      "choices": [
        {
          "delta": {
            "tool_calls": [
              {
                "index": 0,
                "id": "call_a",
                "type": "function",
                "function": {"name": "list_projects", "arguments": '{"limit": 2}'},
              }
            ]
          }
        }
      ]
    },
  ]


def test_chat_url_joins_with_and_without_trailing_slash():
  assert _chat_url("https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1/chat/completions"
  assert (
    _chat_url("https://generativelanguage.googleapis.com/v1beta/openai/")
    == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
  )


class _FakeResponse:
  def __init__(self, status_code, text):
    self.status_code = status_code
    self.text = text

  def read(self):
    return self.text.encode("utf-8")

  def iter_lines(self):
    return []


def test_error_text_shows_the_real_joined_url():
  err = _error_text("https://openrouter.ai/api/v1", 500, "boom")
  assert "https://openrouter.ai/api/v1/chat/completions" in err
  # ...never the misleading no-separator form:
  assert "/api/v1chat" not in err


def test_error_text_429_includes_rate_limit_hint():
  err = _error_text(
    "https://openrouter.ai/api/v1",
    429,
    '{"error":{"message":"Rate limit exceeded: free-models-per-day"}}',
  )
  assert "HTTP 429" in err
  assert "LLM_PROVIDER=local" in err
  assert "LLM_GEMINI_API_KEY" in err


def test_provider_reports_429_with_hint(provider, monkeypatch):
  class _FakeClient429:
    def post(self, url, **kwargs):
      assert url == "https://openrouter.ai/api/v1/chat/completions"
      return _FakeResponse(429, '{"error":{"message":"free-models-per-day"}}')

    def stream(self, method, url, **kwargs):
      raise AssertionError("stream should not be used for the non-streaming path")

  monkeypatch.setattr(provider, "_client", _FakeClient429())
  provider.model = "openrouter/free"

  with pytest.raises(RuntimeError, match="free-models-per-day"):
    provider.create_chat_completion([{"role": "user", "content": "hi"}], max_tokens=16)


def test_merge_delta_tool_calls_multiple_indexes():
  pending = {}
  merge_delta_tool_calls(pending, [
    {"index": 1, "id": "call_b", "function": {"name": "tool_b", "arguments": "{"}},
    {"index": 0, "id": "call_a", "function": {"name": "tool_a", "arguments": ""}},
  ])
  merge_delta_tool_calls(pending, [
    {"index": 1, "function": {"arguments": "}"}},
    {"index": 0, "function": {"arguments": "{}"}},
  ])

  assert pending[0]["function"]["arguments"] == "{}"
  assert pending[1]["function"]["arguments"] == "{}"
  assert pending[0]["id"] == "call_a"
  assert pending[1]["id"] == "call_b"


def test_complete_tool_calls_defaults_pending_id():
  pending = {0: {"index": 0, "id": None, "type": "function", "function": {"name": "t", "arguments": ""}}}
  calls = _complete_tool_calls(pending)

  assert calls == [{"index": 0, "id": "call_0", "type": "function", "function": {"name": "t", "arguments": ""}}]


def test_stream_never_emits_empty_text_chunks():
  lines = [
    "data: {\"choices\": [{\"delta\": {\"role\": \"assistant\"}}]}",
    "data: {\"choices\": [{\"delta\": {\"content\": \"\"}}]}",
    "data: {\"choices\": [{\"delta\": {}}]}",
    "data: [DONE]",
  ]
  assert list(iter_stream_chunks(lines)) == []