from typing import Any, Optional

from utils.env import ENV
from utils.providers.base import LLMProvider


class LocalLLMProvider(LLMProvider):

  # Qwen3 GGUF emits "<thinking>\n...\nresponse\n..." — hide the preamble.
  stream_marker = "response"

  def __init__(self) -> None:
    from llama_cpp import Llama

    self._llm = Llama(
      model_path=ENV.get_model_path(),
      n_ctx=int(ENV.get("MODEL_CONTEXT_WINDOW", default=4096)),
      n_threads=int(ENV.get("MODEL_CPU_THREADS", default=8)),
      n_gpu_layers=int(ENV.get("MODEL_GPU_LAYERS", default=0)),
      verbose=ENV.get("MODEL_VERBOSE", default="false").lower()
      in {"1", "true", "yes"},
    )

  @staticmethod
  def _normalize(messages: list[dict]) -> list[dict]:
    """Keep the exact message shape this local backend already understands.

    Tool messages are sent as ``{"role": "tool", "content": ...}`` (the Qwen3
    template renders them as ``<tool_response>``); the ``name``/``tool_call_id``
    keys the agent may add for remote providers are dropped unless an explicit
    ``tool_call_id`` exists for native OpenAI-style tool calls.
    """
    out = []
    for message in messages:
      if message.get("role") == "tool":
        normalized = {"role": "tool", "content": message.get("content")}
        if message.get("tool_call_id"):
          normalized["tool_call_id"] = message["tool_call_id"]
        out.append(normalized)
      else:
        out.append(message)
    return out

  def create_chat_completion(
    self,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    max_tokens: int = 1024,
    stream: bool = False,
  ) -> Any:
    return self._llm.create_chat_completion(
      messages=self._normalize(messages),
      tools=tools,
      max_tokens=max_tokens,
      stream=stream,
    )