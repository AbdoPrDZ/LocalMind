"""``LLM_PROVIDER=zen`` — the OpenCode Zen gateway provider.

Focuses on the zen provider's *contract*: a single OpenAI-compatible endpoint
with one API key, model from ``LLM_ZEN_MODEL`` (default a free tier), optional
``LLM_ZEN_BASE_URL`` override, and a friendly error when the key is missing. It
inherits the ``openai`` provider's HTTP/SSE machinery, so chat/tool-call
behavior is covered by ``test_openai_provider.py``. Live network calls are not
made here.
"""

import pytest

from utils.env import DEFAULT_ZEN_BASE_URL
from utils.providers.zen import DEFAULT_ZEN_MODEL, ZenLLMProvider


def _unset_keys(monkeypatch):
  monkeypatch.delenv("LLM_OPENCODE_API_KEY", raising=False)
  monkeypatch.delenv("OPENCODE_API_KEY", raising=False)


def test_provider_uses_zen_defaults(monkeypatch):
  _unset_keys(monkeypatch)
  monkeypatch.delenv("LLM_ZEN_MODEL", raising=False)
  monkeypatch.delenv("LLM_ZEN_BASE_URL", raising=False)
  provider = ZenLLMProvider()
  assert provider.model == DEFAULT_ZEN_MODEL
  assert provider._zen_base_url == DEFAULT_ZEN_BASE_URL
  # One endpoint for every model: no gemini special-casing.
  with pytest.raises(ValueError, match="LLM_OPENCODE_API_KEY"):
    provider._endpoint()


def test_provider_model_from_env(monkeypatch):
  _unset_keys(monkeypatch)
  monkeypatch.setenv("LLM_ZEN_MODEL", "qwen3.6-plus")
  assert ZenLLMProvider().model == "qwen3.6-plus"


def test_provider_base_url_override(monkeypatch):
  _unset_keys(monkeypatch)
  monkeypatch.setenv("LLM_ZEN_BASE_URL", "https://example.com/v1/")
  assert ZenLLMProvider()._zen_base_url == "https://example.com/v1"


def test_provider_uses_env_key(monkeypatch):
  monkeypatch.setenv("LLM_OPENCODE_API_KEY", "zen-key-123")
  base_url, api_key = ZenLLMProvider()._endpoint()
  assert base_url == DEFAULT_ZEN_BASE_URL
  assert api_key == "zen-key-123"


def test_provider_falls_back_to_plain_key(monkeypatch):
  monkeypatch.delenv("LLM_OPENCODE_API_KEY", raising=False)
  monkeypatch.setenv("OPENCODE_API_KEY", "plain-key-456")
  _, api_key = ZenLLMProvider()._endpoint()
  assert api_key == "plain-key-456"


def test_reuses_openai_error_text():
  from utils.providers.openai import _error_text

  err = _error_text(DEFAULT_ZEN_BASE_URL, 401, "bad key")
  assert f"{DEFAULT_ZEN_BASE_URL}/chat/completions" in err
  assert "HTTP 401" in err


def test_explains_opencode_only_models():
  provider = ZenLLMProvider()
  explained = provider._explain(
    f"{DEFAULT_ZEN_BASE_URL}/chat/completions failed with HTTP 400: "
    '{"error": {"type": "MissingSessionID", "message": '
    '"OpenCode\'s free tier can only be used in OpenCode"}}'
  )
  assert "free tier" in explained.lower()
  assert "LLM_ZEN_MODEL" in explained
  # Non-free-tier errors pass through unchanged.
  plain = "https://example.com failed with HTTP 500: boom"
  assert provider._explain(plain) == plain


def test_headers_without_session_id(monkeypatch):
  _unset_keys(monkeypatch)
  monkeypatch.delenv("LLM_ZEN_SESSION_ID", raising=False)
  monkeypatch.delenv("OPENCODE_SESSION_ID", raising=False)
  headers = ZenLLMProvider()._headers("k")
  assert headers["Authorization"] == "Bearer k"
  assert "x-opencode-session" not in headers


def test_headers_with_session_id(monkeypatch):
  _unset_keys(monkeypatch)
  monkeypatch.setenv("LLM_ZEN_SESSION_ID", "session-abc-123")
  headers = ZenLLMProvider()._headers("k")
  assert headers["x-opencode-session"] == "session-abc-123"


def test_headers_accepts_plain_session_var(monkeypatch):
  _unset_keys(monkeypatch)
  monkeypatch.delenv("LLM_ZEN_SESSION_ID", raising=False)
  monkeypatch.setenv("OPENCODE_SESSION_ID", "openviking")
  assert ZenLLMProvider()._headers("k")["x-opencode-session"] == "openviking"


def test_settings_resolve_model_zen(monkeypatch):
  from services.settings import resolve_model

  monkeypatch.delenv("LLM_ZEN_MODEL", raising=False)
  assert resolve_model("zen") == DEFAULT_ZEN_MODEL


def test_zen_registered_in_factory():
  from utils.providers import PROVIDERS

  assert PROVIDERS["zen"] is ZenLLMProvider