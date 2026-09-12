"""Provider contract for the LLM layer.

Every provider exposes one method, ``create_chat_completion``, that behaves
like OpenAI's chat completions API so the ``Agent`` stays provider-agnostic:

- non-streaming returns ``{"choices": [{"message": {...}}]}`` with
  ``message`` optionally carrying ``tool_calls`` (OpenAI shape);
- streaming returns an iterator of ``{"choices": [{"delta": {...}}]}``
  chunks, where ``delta`` may carry ``content`` and/or ``tool_calls``.
"""

from typing import Any, Optional


class LLMProvider:

  #: Optional marker line (e.g. ``"response"`` for Qwen3) that separates the
  #: model's hidden thinking preamble from the visible answer. When set, the
  #: agent hides everything before the marker while streaming. ``None`` means
  #: the model has no preamble and text can be streamed verbatim.
  stream_marker: Optional[str] = None

  def create_chat_completion(
    self,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    max_tokens: int = 1024,
    stream: bool = False,
  ) -> Any:
    raise NotImplementedError("Subclasses must implement create_chat_completion")