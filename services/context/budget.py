"""Token-aware context budgeting.

The model reports a context window (``LLM_LOCAL_CONTEXT_WINDOW`` for local).
Every section fed into the prompt is bounded by a share of that window so a
long conversation, tool noise, or a big fetched page can never blow it. Since
providers tokenize differently, a simple character estimate
(``len(text) / CHARS_PER_TOKEN``) is used as a safe, provider-independent
proxy: over-estimating slightly only costs a little spare room.
"""

import math

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
  """Conservative token estimate from raw character count."""
  if not text:
    return 0
  return math.ceil(len(text) / CHARS_PER_TOKEN)


def trim_to_chars(text: str, cap_chars: int) -> str:
  """Bound a section to ``cap_chars`` characters, dropping the tail (end)."""
  text = (text or "").strip()
  if len(text) <= cap_chars:
    return text
  return text[-cap_chars:].strip()


class ContextBudget:
  """Tracks the input-token budget for one ``send``.

  ``remaining_chars`` starts at ``(model_context_window -
  reserved_output_tokens) * CHARS_PER_TOKEN`` and shrinks as each section is
  consumed. Sections are added newest-first so that, under pressure, the
  oldest context is dropped first — never the beginning of a message.
  """

  def __init__(
    self,
    model_context_window: int,
    reserved_output_tokens: int = 1024,
  ) -> None:
    if model_context_window <= 0:
      raise ValueError("model_context_window must be positive")
    self.model_context_window = model_context_window
    self.reserved_output_tokens = max(reserved_output_tokens, 0)
    input_tokens = max(model_context_window - self.reserved_output_tokens, 1)
    self.remaining_chars = input_tokens * CHARS_PER_TOKEN

  @property
  def remaining_tokens(self) -> int:
    return math.ceil(self.remaining_chars / CHARS_PER_TOKEN)

  def fits(self, text: str) -> bool:
    return len((text or "").strip()) <= self.remaining_chars

  def consume(self, text: str) -> str:
    """Reserve budget for ``text`` and return it trimmed to what remained."""
    text = (text or "").strip()
    if not text:
      return ""
    text = trim_to_chars(text, self.remaining_chars)
    self.remaining_chars -= len(text)
    return text

  def debug(self) -> dict:
    return {
      "window": self.model_context_window,
      "reserved_output_tokens": self.reserved_output_tokens,
      "remaining_tokens": self.remaining_tokens,
      "remaining_chars": self.remaining_chars,
    }