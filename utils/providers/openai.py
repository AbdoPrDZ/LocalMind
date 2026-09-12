"""OpenAI-compatible provider: free routers and Gemini through one interface.

Speaks OpenAI chat completions over HTTP (httpx). The model id selects the
backend:

- ``gemini-...`` models  → Gemini's OpenAI-compatible endpoint
  (``https://generativelanguage.googleapis.com/v1beta/openai/``) using
  ``GEMINI_API_KEY``;
- any other model        → the configured free/OpenAI-compatible router
  (default OpenRouter, ``https://openrouter.ai/api/v1``) using
  ``OPENAI_API_KEY`` (or ``OPENROUTER_API_KEY``) and ``OPENAI_BASE_URL``.

Non-streaming and streaming (SSE) responses keep the OpenAI chat-completions
shape, so the ``Agent`` stays provider-agnostic. Native ``tool_calls`` pass
through unchanged; during streaming, index-based tool-call fragments from
servers like OpenRouter are merged into complete calls before being handed
back, matching how the Gemini provider already buffers pending calls.
"""

import json
from typing import Any, Iterable, Iterator, Optional

from utils.env import (
  DEFAULT_OPENAI_BASE_URL,
  DEFAULT_GEMINI_OPENAI_BASE_URL,
  DEFAULT_OPENAI_MODEL,
  ENV,
)
from utils.providers.base import LLMProvider

_STREAM_TIMEOUT_SECONDS = 300.0


def is_gemini_model(model: str) -> bool:
  """True when a model id belongs to Gemini (any ``gemini-*`` flavor)."""
  return (model or "").strip().lower().startswith("gemini")


def merge_delta_tool_calls(pending: dict, deltas: Optional[list]) -> None:
  """Merge OpenRouter-style index-based ``delta.tool_calls`` fragments.

  Each streaming delta carries one ``{index, id?, function{name?, arguments?}}``
  entry; accumulate fragments per index so the assistant message we emit holds
  complete, executable calls.
  """
  if not deltas:
    return
  if not isinstance(pending, dict):
    return

  for tool_call in deltas:
    if not isinstance(tool_call, dict):
      continue

    index = tool_call.get("index")
    if index is None:
      index = len(pending)

    entry = pending.get(index)
    if entry is None:
      entry = pending[index] = {
        "index": index,
        "id": None,
        "type": "function",
        "function": {"name": None, "arguments": ""},
      }

    if tool_call.get("id"):
      entry["id"] = tool_call["id"]

    fn = tool_call.get("function") or {}
    if isinstance(fn, dict):
      if fn.get("name"):
        entry["function"]["name"] = fn["name"]
      arguments = fn.get("arguments")
      if arguments:
        entry["function"]["arguments"] += arguments


def _complete_tool_calls(pending: dict) -> Optional[list]:
  """Finalize buffered fragments into OpenAI-shaped tool call entries."""
  if not pending:
    return None
  calls = []
  for index in sorted(pending):
    call = pending[index]
    if not call["id"]:
      call["id"] = f"call_{index}"
    calls.append(call)
  return calls


def _chat_url(base_url: str) -> str:
  """Join a base url (with or without trailing slash) to ``chat/completions``."""
  return f"{base_url.rstrip('/')}/chat/completions"


def iter_stream_chunks(lines: Iterable[str]) -> Iterator[dict]:
  """Parse raw SSE ``data:`` lines into normalized OpenAI-style chunks.

  Yields ``{"choices": [{"delta": {"content": ...}}]}`` for each text token,
  standalone ``{"usage": {...}}`` chunks when the server reports them, and a
  final ``{"choices": [{"delta": {"tool_calls": [...]}}]}`` chunk with the
  merged tool calls once the stream ends.
  """
  pending: dict = {}

  for line in lines:
    if not line or not line.startswith("data:"):
      continue
    data = line[5:].strip()
    if not data or data == "[DONE]":
      continue

    try:
      chunk = json.loads(data)
    except json.JSONDecodeError:
      continue

    if not isinstance(chunk, dict):
      continue

    if chunk.get("usage"):
      yield {"usage": chunk["usage"]}

    for choice in chunk.get("choices") or []:
      if not isinstance(choice, dict):
        continue
      delta = choice.get("delta") or {}
      if not isinstance(delta, dict):
        continue

      content = delta.get("content")
      if content:
        yield {"choices": [{"delta": {"content": content}}]}

      merge_delta_tool_calls(pending, delta.get("tool_calls"))

  calls = _complete_tool_calls(pending)
  if calls:
    yield {"choices": [{"delta": {"tool_calls": calls}}]}


class OpenAILLMProvider(LLMProvider):

  # Plain chat-completions text: stream verbatim, no thinking preamble.
  stream_marker = None

  def __init__(self) -> None:
    self.model = ENV.get("OPENAI_MODEL", default=DEFAULT_OPENAI_MODEL) or DEFAULT_OPENAI_MODEL
    self._openai_base_url = ENV.get("OPENAI_BASE_URL", default=DEFAULT_OPENAI_BASE_URL)
    self._openai_api_key = ENV.get("OPENAI_API_KEY") or ENV.get("OPENROUTER_API_KEY")
    self._gemini_base_url = ENV.get(
      "GEMINI_OPENAI_BASE_URL",
      default=DEFAULT_GEMINI_OPENAI_BASE_URL,
    )
    self._gemini_api_key = ENV.get("GEMINI_API_KEY")

    import httpx

    self._httpx = httpx
    self._client = httpx.Client(timeout=httpx.Timeout(_STREAM_TIMEOUT_SECONDS))

  def _endpoint(self) -> tuple[str, str]:
    """Resolve ``(base_url, api_key)`` for the current model."""
    if is_gemini_model(self.model):
      key = self._gemini_api_key
      if not key:
        raise ValueError(
          "GEMINI_API_KEY is required when using a Gemini model through "
          "LLM_PROVIDER=openai. Get one at https://aistudio.google.com/apikey."
        )
      return self._gemini_base_url, key

    key = self._openai_api_key
    if not key:
      raise ValueError(
        "OPENAI_API_KEY (or OPENROUTER_API_KEY) is required when using free "
        "models through LLM_PROVIDER=openai."
      )
    return self._openai_base_url, key

  def _headers(self, api_key: str) -> dict:
    request_headers = {
      "Authorization": f"Bearer {api_key}",
      "Content-Type": "application/json",
    }
    referer = ENV.get("OPENAI_REFERER", default="https://github.com/LocalMind")
    title = ENV.get("OPENAI_TITLE", default="LocalMind")
    return {
      **request_headers,
      "HTTP-Referer": referer,
      "X-Title": title,
    }

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

  def _chat(
    self,
    messages: list[dict],
    tools: Optional[list[dict]],
    max_tokens: int,
  ) -> dict:
    base_url, api_key = self._endpoint()
    response = self._client.post(
      _chat_url(base_url),
      headers=self._headers(api_key),
      json=self._payload(self.model, messages, tools, max_tokens, stream=False),
    )

    if response.status_code != 200:
      body = response.text[:500]
      raise RuntimeError(
        f"{base_url}chat/completions failed with HTTP {response.status_code}: {body}"
      )

    return response.json()

  def _stream(
    self,
    messages: list[dict],
    tools: Optional[list[dict]],
    max_tokens: int,
  ) -> Iterator[dict]:
    base_url, api_key = self._endpoint()
    with self._client.stream(
      "POST",
      _chat_url(base_url),
      headers=self._headers(api_key),
      json=self._payload(self.model, messages, tools, max_tokens, stream=True),
    ) as response:
      if response.status_code != 200:
        body = response.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
          f"{base_url}chat/completions failed with HTTP {response.status_code}: {body}"
        )
      yield from iter_stream_chunks(response.iter_lines())

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