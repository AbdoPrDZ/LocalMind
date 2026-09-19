"""``zen`` LLM provider: OpenCode Zen, the OpenAI-compatible gateway.

``LLM_PROVIDER=zen`` routes every request through OpenCode Zen
(https://opencode.ai/zen) — a curated, pay-per-use gateway of tested models
plus a few free tiers, reachable with a single API key. It is a thin subclass
of the ``openai`` provider that points the same HTTP/SSE machinery at Zen's
completions endpoint, so the Agent/tool loop is unchanged.

Configuration (``.env``):

- ``LLM_OPENCODE_API_KEY`` (required; plain ``OPENCODE_API_KEY`` is also
  accepted) — create a key at https://opencode.ai/zen.
- ``LLM_ZEN_MODEL`` — a model id from https://opencode.ai/zen/v1/models
  (default ``DEFAULT_ZEN_MODEL``, a free tier). Examples: ``gemini-3.5-flash``,
  ``qwen3.6-plus``, ``claude-sonnet-4-5``.
- ``LLM_ZEN_BASE_URL`` (optional) — override the default
  ``https://opencode.ai/zen/v1``.
- ``LLM_ZEN_SESSION_ID`` (optional) — a stable OpenCode session id sent as the
  ``x-opencode-session`` header (``OPENCODE_SESSION_ID`` is a fallback). Zen
  requires that header on every request; without it free-tier models are
  rejected with ``MissingSessionID``. Any stable string keeps prompt-cache
  routing working; a real OpenCode session id is what unlocks free-tier models
  outside the OpenCode app.

Zen speaks OpenAI chat-completions for most catalog models, so non-streaming
and SSE streaming answers (including native ``tool_calls``) keep the standard
shape. A few Zen families (the ``gpt-*`` Responses-API models, native
Anthropic/Google endpoints) are not served through ``/v1/chat/completions``
and will not work with this provider — pick another catalog id. Usage tokens
are still counted; cost is only estimated for ``gemini-*`` ids (the pricing
table matches and Zen charges no markup), other models report cost 0.

Free-tier caveat: Zen's free/contributor models (``*-free``, OpenCode's own
ids like ``big-pickle``) are only usable inside the OpenCode app and reject
raw API calls with an OpenCode ``MissingSessionID`` error. This provider
detects those rejections and explains them; to actually get replies through
the API, set ``LLM_ZEN_MODEL`` to a paid catalog id.
"""

from typing import Any, Optional

from utils.env import DEFAULT_ZEN_BASE_URL, ENV
from utils.providers.openai import OpenAILLMProvider

DEFAULT_ZEN_MODEL = "deepseek-v4-flash-free"

#: Bodies Zen returns when a free-tier/OpenCode-only model is called from
#: outside the OpenCode app (observed on ``*``-free and ``big-pickle`` ids).
_OPENCODE_ONLY_MARKERS = (
  "missing session",
  "free tier can only be used in opencode",
  "model is unavailable",
  "modelerror",
)


class ZenLLMProvider(OpenAILLMProvider):

  # Plain chat-completions text: stream verbatim, no thinking preamble.
  stream_marker = None

  def __init__(self) -> None:
    super().__init__()
    self.model = (ENV.get("LLM_ZEN_MODEL") or DEFAULT_ZEN_MODEL).strip()
    self._zen_base_url = (ENV.get("LLM_ZEN_BASE_URL") or DEFAULT_ZEN_BASE_URL).rstrip("/")
    self._zen_api_key = ENV.get("LLM_OPENCODE_API_KEY") or ENV.get("OPENCODE_API_KEY")

  def _endpoint(self) -> tuple[str, str]:
    """Zen is a single OpenAI-compatible endpoint: base url + key only."""
    if not self._zen_api_key:
      raise ValueError(
        "LLM_OPENCODE_API_KEY is required when LLM_PROVIDER=zen. "
        "Create a key at https://opencode.ai/zen."
      )
    return self._zen_base_url, self._zen_api_key

  def _headers(self, api_key: str) -> dict:
    """Authorization + the ``x-opencode-session`` header Zen requires."""
    headers = {
      "Authorization": f"Bearer {api_key}",
      "Content-Type": "application/json",
    }
    session_id = (ENV.get("LLM_ZEN_SESSION_ID") or ENV.get("OPENCODE_SESSION_ID") or "").strip()
    if session_id:
      headers["x-opencode-session"] = session_id
    return headers

  @staticmethod
  def _explain(message: str) -> str:
    """Append actionable guidance when Zen rejects an OpenCode-only model."""
    lowered = (message or "").lower()
    if any(marker in lowered for marker in _OPENCODE_ONLY_MARKERS):
      return (
        f"{message} | This is a Zen free-tier/OpenCode-only model: it can only "
        "be used inside the OpenCode app, not through the API. Set "
        "LLM_ZEN_MODEL to a paid catalog id (see "
        "https://opencode.ai/zen/v1/models), or switch to a cost-0 backend "
        "(LLM_PROVIDER=free or LLM_PROVIDER=openai with an OpenRouter key)."
      )
    return message

  def create_chat_completion(
    self,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    max_tokens: int = 1024,
    stream: bool = False,
  ) -> Any:
    try:
      return super().create_chat_completion(messages, tools, max_tokens, stream)
    except RuntimeError as exc:
      raise RuntimeError(self._explain(str(exc))) from exc