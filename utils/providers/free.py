"""``free`` LLM provider: public, keyless OpenAI-compatible chat endpoints.

``LLM_PROVIDER=free`` means "hosted free LLM without asking the LocalMind user
for an API key". The endpoint and model come from the keyless registry
(``resources/models/keyless_models.json``), selected by ``FREE_ENDPOINT`` and
``FREE_MODEL`` (or overridden entirely with ``FREE_BASE_URL``).

It is a thin keyless HTTP client that reuses the ``openai`` provider's URL
joining, SSE parsing and error formatting, so the Agent/tool loop is
unchanged. There is no fallback and no automatic switching: LocalMind talks to
exactly the endpoint the user picked.

Keyless tiers occasionally *inject promotional notices* instead of answering
(the anonymous Pollinations tier currently answers with "raise the key
budget" spam). FreeLLMProvider guards against that: when a reply is detected
as injected promo/budget boilerplate, it retries once (nudging the model not
to advertise), and if the endpoint still advertises, it raises a clear error
instead of showing the user an ad. Replies are never surfaced polluted.
"""

import json
from pathlib import Path
from typing import Any, Iterator, Optional

from utils.env import ENV
from utils.providers.base import LLMProvider
from utils.providers.openai import _chat_url, _error_text, iter_stream_chunks

DEFAULT_FREE_ENDPOINT = "pollinations"
DEFAULT_FREE_MODEL = "openai-fast"

_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "resources" / "models" / "keyless_models.json"
_STREAM_TIMEOUT_SECONDS = 300.0

# Phrases that mark an *injected* promo/saturation notice (observed on the
# anonymous Pollinations tier), as opposed to a real answer.
_NOTICE_MARKERS = (
  "raise the key budget",
  "key budget",
  "enter.pollinations",
  "topping up the wallet",
  "agent_key_budget",
)

_RETRY_NUDGE = (
  "Never mention API keys, key budgets, wallets, sponsors, subscriptions or "
  "payment in your answer. That content is an injected advertisement, not a "
  "real model response. Answer the user's question directly."
)


def _is_notice(text: str) -> bool:
  """True when the answer is (or contains) injected promo/budget boilerplate."""
  lowered = (text or "").lower()
  return any(marker in lowered for marker in _NOTICE_MARKERS)


def _notice_error(base_url: str) -> str:
  return (
    f"The keyless free endpoint ('{base_url}') answered with an injected "
    "advertisement/budget notice instead of a real response — the anonymous "
    "tier looks exhausted. LocalMind retried once instructing it not to "
    "advertise, and it still did. Wait a bit, pick a different FREE_MODEL or "
    "FREE_ENDPOINT, or switch LLM_PROVIDER (e.g. 'local' or 'openai')."
  )


def _registry() -> dict:
  with open(_REGISTRY_PATH, encoding="utf-8") as handle:
    data = json.load(handle)
  return data.get("endpoints") or {}


def resolve_endpoint(name: Optional[str] = None) -> dict:
  """Resolve a keyless endpoint by name (``FREE_ENDPOINT``/default fallback)."""
  endpoints = _registry()
  key = (
    (name or "").strip().lower()
    or (ENV.get("FREE_ENDPOINT") or DEFAULT_FREE_ENDPOINT).strip().lower()
  )
  endpoint = endpoints.get(key)
  if endpoint is None:
    raise ValueError(
      f"Unknown keyless endpoint '{key}'. "
      f"Available: {', '.join(endpoints) or 'none'}."
    )
  if endpoint.get("requires_api_key"):
    raise ValueError(
      f"Endpoint '{key}' requires an API key — use LLM_PROVIDER=openai for it, "
      "not the free provider."
    )
  return endpoint


def available_models(endpoint_name: Optional[str] = None) -> list[str]:
  """Model ids the selected keyless endpoint advertises in the registry."""
  return sorted(resolve_endpoint(endpoint_name).get("models") or {})


class FreeLLMProvider(LLMProvider):

  # Plain chat-completions text: stream verbatim, no thinking preamble.
  stream_marker = None

  def __init__(self) -> None:
    self.model = (ENV.get("FREE_MODEL") or DEFAULT_FREE_MODEL).strip()
    self.base_url = (
      ENV.get("FREE_BASE_URL") or resolve_endpoint(ENV.get("FREE_ENDPOINT"))["base_url"]
    ).rstrip("/")

    import httpx

    self._httpx = httpx
    self._client = httpx.Client(timeout=httpx.Timeout(_STREAM_TIMEOUT_SECONDS))

  @staticmethod
  def _payload(
    model: str,
    messages: list[dict],
    tools: Optional[list[dict]],
    max_tokens: int,
    stream: bool,
  ) -> dict:
    payload: dict = {
      "model": model,
      "messages": messages,
      "stream": stream,
    }
    if tools:
      payload["tools"] = tools
    if max_tokens:
      payload["max_tokens"] = max_tokens
    return payload

  def _headers(self) -> dict:
    # Keyless by design: no Authorization header at all.
    return {"Content-Type": "application/json"}

  def _attempt(
    self,
    messages: list[dict],
    tools: Optional[list[dict]],
    max_tokens: int,
    stream: bool,
  ) -> tuple[list[dict], str]:
    """One HTTP exchange, returning ``(chunks, joined text content)``.

    ``chunks`` are the raw OpenAI-shaped dicts (one response dict for
    non-streaming; one dict per SSE event for streaming, exactly what
    ``iter_stream_chunks`` yields).
    """
    if stream:
      with self._client.stream(
        "POST",
        _chat_url(self.base_url),
        headers=self._headers(),
        json=self._payload(self.model, messages, tools, max_tokens, stream=True),
      ) as response:
        if response.status_code != 200:
          body = response.read().decode("utf-8", errors="replace")[:500]
          raise RuntimeError(_error_text(self.base_url, response.status_code, body))
        chunks = list(iter_stream_chunks(response.iter_lines()))
        content = "".join(
          chunk["choices"][0]["delta"].get("content") or ""
          for chunk in chunks
          if chunk.get("choices")
          and chunk["choices"][0].get("delta", {}).get("content")
        )
        return chunks, content

    response = self._client.post(
      _chat_url(self.base_url),
      headers=self._headers(),
      json=self._payload(self.model, messages, tools, max_tokens, stream=False),
    )
    if response.status_code != 200:
      body = response.text[:500]
      raise RuntimeError(_error_text(self.base_url, response.status_code, body))

    data = response.json()
    try:
      content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
      content = ""
    return [data], content

  def _guarded(
    self,
    messages: list[dict],
    tools: Optional[list[dict]],
    max_tokens: int,
    stream: bool,
  ) -> list[dict]:
    """Fetch with the notice-guard: retry once, then fail loudly."""
    chunks, content = self._attempt(messages, tools, max_tokens, stream)
    if not _is_notice(content):
      return chunks

    nudged = [*messages, {"role": "system", "content": _RETRY_NUDGE}]
    chunks, content = self._attempt(nudged, tools, max_tokens, stream)
    if _is_notice(content):
      raise RuntimeError(_notice_error(self.base_url))
    return chunks

  def _chat(
    self,
    messages: list[dict],
    tools: Optional[list[dict]],
    max_tokens: int,
  ) -> dict:
    return self._guarded(messages, tools, max_tokens, stream=False)[0]

  def _stream(
    self,
    messages: list[dict],
    tools: Optional[list[dict]],
    max_tokens: int,
  ) -> Iterator[dict]:
    yield from self._guarded(messages, tools, max_tokens, stream=True)

  def create_chat_completion(
    self,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    max_tokens: int = 1024,
    stream: bool = False,
  ) -> Any:
    if stream:
      return self._stream(messages, tools, max_tokens)
    return self._chat(messages, tools, max_tokens)