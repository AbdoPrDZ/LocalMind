"""``LLM_PROVIDER=free`` — the keyless hosted provider.

Focuses on the free provider's *contract*: no API key anywhere, registry-driven
endpoint + model, OpenAI-shaped payload/response, reuse of the openai
provider's url-join / SSE parser, and the notice-guard that retries injected
promo/budget replies once and surfaces a clear error instead of showing an ad.
Live network calls are not made here.
"""

import json

import pytest

from utils.providers.free import (
  DEFAULT_FREE_ENDPOINT,
  DEFAULT_FREE_MODEL,
  _RETRY_NUDGE,
  FreeLLMProvider,
  _is_notice,
  available_models,
  resolve_endpoint,
)


def test_resolves_default_keyless_endpoint():
  endpoint = resolve_endpoint()
  assert endpoint["base_url"] == "https://text.pollinations.ai/openai"
  assert endpoint.get("requires_api_key") is False
  assert "openai-fast" in endpoint["models"]


def test_resolve_endpoint_by_name():
  assert resolve_endpoint("pollinations")["name"].startswith("Pollinations")


def test_resolve_endpoint_unknown_raises():
  with pytest.raises(ValueError, match="Unknown keyless endpoint"):
    resolve_endpoint("does-not-exist")


def test_available_models_default():
  assert available_models() == ["openai-fast"]


def test_provider_needs_no_api_key(monkeypatch):
  monkeypatch.delenv("FREE_API_KEY", raising=False)
  provider = FreeLLMProvider()
  assert provider.base_url == "https://text.pollinations.ai/openai"
  assert provider.model == DEFAULT_FREE_MODEL
  # Keyless: the request headers carry no Authorization.
  headers = provider._headers()
  assert "Authorization" not in headers
  assert headers["Content-Type"] == "application/json"


def test_provider_model_from_env(monkeypatch):
  monkeypatch.setenv("FREE_MODEL", "qwen-coder")
  assert FreeLLMProvider().model == "qwen-coder"


def test_provider_base_url_override(monkeypatch):
  monkeypatch.setenv("FREE_BASE_URL", "https://example.com/v1/")
  provider = FreeLLMProvider()
  assert provider.base_url == "https://example.com/v1"


def test_payload_shape():
  payload = FreeLLMProvider._payload(
    DEFAULT_FREE_MODEL,
    [{"role": "user", "content": "hi"}],
    tools=[{"type": "function"}],
    max_tokens=64,
    stream=True,
  )
  assert payload == {
    "model": DEFAULT_FREE_MODEL,
    "messages": [{"role": "user", "content": "hi"}],
    "tools": [{"type": "function"}],
    "max_tokens": 64,
    "stream": True,
  }


def test_payload_omits_tools_and_max_tokens_when_empty():
  payload = FreeLLMProvider._payload("m", [], None, 0, stream=False)
  assert "tools" not in payload
  assert "max_tokens" not in payload


def test_error_text_reused_for_free_errors():
  from utils.providers.openai import _error_text

  err = _error_text("https://text.pollinations.ai/openai", 429, "quota")
  assert "https://text.pollinations.ai/openai/chat/completions" in err
  assert "HTTP 429" in err


def test_registry_is_valid_json():
  import json as jsonlib
  from pathlib import Path

  path = Path("resources/models/keyless_models.json")
  data = jsonlib.loads(path.read_text(encoding="utf-8"))
  for name, endpoint in data["endpoints"].items():
    assert isinstance(endpoint.get("base_url"), str)
    assert endpoint.get("requires_api_key") is False
    assert isinstance(endpoint.get("models"), dict)
    assert name  # non-empty key


def test_settings_resolve_model_free(monkeypatch):
  from services.settings import resolve_model

  monkeypatch.delenv("FREE_MODEL", raising=False)
  assert resolve_model("free") == DEFAULT_FREE_MODEL


# --- notice-guard ---

NOTICE_TEXT = (
  "The API key used for this request has reached its budget. Please "
  "[raise the key budget](https://enter.pollinations.ai/edit-key?ref=agent_key_budget), "
  "then try again. Topping up the wallet does not raise this limit."
)
PLAIN_TEXT = "PROBE_OK"


def test_is_notice_detects_injected_promo_and_not_real_text():
  assert _is_notice(NOTICE_TEXT)
  assert not _is_notice(PLAIN_TEXT)
  assert not _is_notice("Here is how you plan a household budget.")
  assert not _is_notice("")


class _FakePostResponse:
  def __init__(self, content):
    self.status_code = 200
    self.text = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]})
    self.content = content

  def json(self):
    return json.loads(self.text)


class _FakeStreamResponse:
  def __init__(self, content):
    self.status_code = 200
    self.lines = [
      f"data: {json.dumps({'choices': [{'delta': {'content': content}}]})}",
      "data: [DONE]",
    ]

  def __enter__(self):
    return self

  def __exit__(self, *exc):
    return False

  def iter_lines(self):
    return iter(self.lines)

  def read(self):
    return b""


class _QueueClient:
  """Returns responses in order; records request payloads."""

  def __init__(self, responses):
    self._responses = list(responses)
    self.calls = []

  def post(self, url, headers=None, json=None, **kwargs):
    self.calls.append(json)
    response = self._responses.pop(0)
    if isinstance(response, Exception):
      raise response
    return response

  def stream(self, method, url, headers=None, json=None, **kwargs):
    self.calls.append(json)
    response = self._responses.pop(0)
    if isinstance(response, Exception):
      raise response
    return response


def _provider_with(client):
  provider = FreeLLMProvider()
  provider._client = client
  return provider


def test_chat_retries_once_on_notice_and_uses_nudged_messages():
  client = _QueueClient([_FakePostResponse(NOTICE_TEXT), _FakePostResponse(PLAIN_TEXT)])
  provider = _provider_with(client)

  result = provider._chat([{"role": "user", "content": "hi"}], None, 64)

  assert result["choices"][0]["message"]["content"] == PLAIN_TEXT
  assert len(client.calls) == 2
  assert client.calls[1]["messages"][-1] == {"role": "system", "content": _RETRY_NUDGE}


def test_chat_clean_answer_is_single_call():
  client = _QueueClient([_FakePostResponse(PLAIN_TEXT)])
  provider = _provider_with(client)

  result = provider._chat([{"role": "user", "content": "hi"}], None, 64)

  assert result["choices"][0]["message"]["content"] == PLAIN_TEXT
  assert len(client.calls) == 1


def test_chat_still_polluted_raises_clear_error():
  client = _QueueClient([_FakePostResponse(NOTICE_TEXT), _FakePostResponse(NOTICE_TEXT)])
  provider = _provider_with(client)

  with pytest.raises(RuntimeError, match="injected advertisement/budget notice"):
    provider._chat([{"role": "user", "content": "hi"}], None, 64)
  assert len(client.calls) == 2


def test_stream_retries_and_drops_polluted_bytes():
  client = _QueueClient([_FakeStreamResponse(NOTICE_TEXT), _FakeStreamResponse(PLAIN_TEXT)])
  provider = _provider_with(client)

  chunks = list(provider._stream([{"role": "user", "content": "hi"}], None, 64))

  joined = "".join(c["choices"][0]["delta"]["content"] for c in chunks)
  assert joined == PLAIN_TEXT
  assert _is_notice(joined) is False
  assert len(client.calls) == 2
  assert client.calls[1]["messages"][-1] == {"role": "system", "content": _RETRY_NUDGE}


def test_stream_still_polluted_raises_clear_error():
  client = _QueueClient([_FakeStreamResponse(NOTICE_TEXT), _FakeStreamResponse(NOTICE_TEXT)])
  provider = _provider_with(client)

  with pytest.raises(RuntimeError, match="injected advertisement/budget notice"):
    list(provider._stream([{"role": "user", "content": "hi"}], None, 64))
  assert len(client.calls) == 2