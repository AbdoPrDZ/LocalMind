import json
import re
from typing import Optional
from collections.abc import Generator

from utils.env import ENV
from utils.llm import get_llm
from utils.tool import Tool, tool_error

TOOL_CALL_RE = re.compile(
  r"<tool_call>(.*?)</tool_call>",
  re.DOTALL,
)

# Qwen3 emits " thinking\n...\nresponse\n<reply>": the answer separator is a
# "response" marker line (spaces tolerated), everything before it is thinking.
RESPONSE_MARKER_RE = re.compile(r"^\s*response\s*$\n?", re.MULTILINE)

#: Loop guardrails (Phase 4): hard cap on tool-loop iterations and on repeated
#: identical tool calls; per-result serialization bound for the model context.
MAX_AGENT_STEPS_DEFAULT = 8
MAX_IDENTICAL_TOOL_CALLS = 3
MAX_TOOL_RESULT_CHARS = 6_000

LOOP_LIMIT_ANSWER = (
  "I hit my step/repetition limit while trying to fulfil that — please "
  "narrow the request or rephrase it."
)


class _ToolLoopAbort(Exception):
  pass


def _max_agent_steps() -> int:
  try:
    return max(int(ENV.get("MAX_AGENT_STEPS", default=str(MAX_AGENT_STEPS_DEFAULT))), 1)
  except (TypeError, ValueError):
    return MAX_AGENT_STEPS_DEFAULT


def _call_signature(name: str, arguments) -> tuple[str, str]:
  """Stable identity for a tool call so repetitions are detectable."""
  try:
    encoded = json.dumps(arguments, sort_keys=True, default=str)
  except (TypeError, ValueError):
    encoded = "_unserializable"
  return name, encoded


def _serialize_tool_result(result) -> str:
  """Serialize a tool result for the model, bounded to protect the context."""
  try:
    text = json.dumps(result, ensure_ascii=False, default=str)
  except (TypeError, ValueError):
    text = str(result)
  if len(text) > MAX_TOOL_RESULT_CHARS:
    return text[:MAX_TOOL_RESULT_CHARS] + "\n...[truncated]"
  return text


def parse_tool_calls(content: Optional[str]) -> list[dict]:
  """Extract tool calls from the model's raw text.

  Your Qwen3 GGUF emits tool calls as "<tool_call>...json...</tool_call>"
  blocks that this build of llama-cpp-python does not parse into
  structured tool_calls, so we parse them here.
  """
  calls = []

  for raw in TOOL_CALL_RE.findall(content or ""):
    try:
      call = json.loads(raw.strip())
    except json.JSONDecodeError:
      continue

    if isinstance(call, dict) and call.get("name"):
      calls.append(call)

  return calls


def clean_answer(content: Optional[str]) -> str:
    """Remove Qwen3 thinking/reasoning blocks and return the final answer.

    Handles the common reasoning formats a model may place in front of the
    actual reply before it reaches the user:

    - `<thinking>...</thinking>` blocks
    - the bare ``thinking ... response`` preamble
    - Qwen3 template segments (`<|im_start|>think ... <|im_start|>answer`)
    - a standalone ``response`` marker line
    """
    content = (content or "").strip()

    content = re.sub(
      r"<thinking>.*?</thinking>",
      "",
      content,
      flags=re.DOTALL | re.IGNORECASE,
    )

    content = re.sub(
      r"<\|im_start\|>think.*?<\|im_start\|>answer",
      "",
      content,
      flags=re.DOTALL | re.IGNORECASE,
    )

    content = re.sub(
      r"(?:\A|^)thinking.*?response",
      "",
      content,
      flags=re.DOTALL | re.IGNORECASE,
    )

    content = re.sub(
      r"^\s*response\s*$",
      "",
      content,
      flags=re.MULTILINE | re.IGNORECASE,
    )

    return content.strip()


class Agent:
  def __init__(
    self,
    tools: list[Tool],
    system_prompt: str,
    max_tokens: int = 1024,
  ) -> None:
    self.tools = tools
    self.tool_map = {tool.name: tool for tool in tools}
    self.system_prompt = system_prompt
    self.max_tokens = max_tokens
    self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    self.tool_results: list[dict] = []

  def _record_tool_result(self, name: str, result) -> None:
    """Remember a successful tool result for this send.

    The Chat service folds these into the running chat context and global
    memory, so data gathered through tools survives across turns even when the
    model emits no ``<context>`` block of its own.
    """
    if isinstance(result, dict) and (result.get("error") or result.get("ok") is False):
      return
    self.tool_results.append({"name": name, "result": result})

  def take_tool_results(self) -> list[dict]:
    """Return the tool results recorded during the last run and reset them."""
    results = self.tool_results
    self.tool_results = []
    return results

  def _accumulate_usage(self, usage) -> None:
    """Add a provider ``usage`` dict to the running totals for this send."""
    if not usage:
      return
    prompt = max(int(usage.get("prompt_tokens") or 0), 0)
    completion = max(int(usage.get("completion_tokens") or 0), 0)
    self.usage["prompt_tokens"] += prompt
    self.usage["completion_tokens"] += completion
    self.usage["total_tokens"] += prompt + completion

  def take_usage(self) -> dict:
    """Return the accumulated usage for the last run and reset the counter."""
    usage = dict(self.usage)
    self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return usage

  def _execute_tool_calls(
    self,
    tool_calls: list[dict],
    messages: list[dict],
    called: dict,
  ) -> None:
    """Execute parsed tool calls, append tool results, guard against loops.

    Raises ``_ToolLoopAbort`` when the model repeats the exact same
    ``name + arguments`` call too many times in a row.
    """
    for call in tool_calls:
      fn = call.get("function", call)

      name = fn.get("name", "")

      arguments = fn.get("arguments", {})
      if isinstance(arguments, str):
        try:
          arguments = json.loads(arguments)
        except json.JSONDecodeError:
          arguments = {}

      signature = _call_signature(name, arguments)
      called[signature] = called.get(signature, 0) + 1
      if called[signature] > MAX_IDENTICAL_TOOL_CALLS:
        raise _ToolLoopAbort()

      tool = self.tool_map.get(name)

      if tool is None:
        result = tool_error("unknown_tool", f"Unknown tool: {name}")
      else:
        result = tool.call(arguments)

      self._record_tool_result(name, result)

      # OpenAI-style tool response. `name` lets remote providers pair the
      # result with the right function; `tool_call_id` grounds native calls.
      messages.append({
        "role": "tool",
        "name": name,
        "tool_call_id": call.get("id"),
        "content": _serialize_tool_result(result),
      })

  def run(self, messages: list[dict]) -> str:
    """Run the tool-loop against a full message history.

    ``messages`` is a chat-completion list that already includes the system
    prompt and all prior turns (see ``apps.base.Chat.send``). The loop appends
    the assistant's choices and tool results as it iterates, so the model sees
    its own previous calls on every step.
    """
    llm = get_llm()

    self.tool_results = []

    called: dict = {}
    steps = 0
    max_steps = _max_agent_steps()

    while True:
      steps += 1
      if steps > max_steps:
        return LOOP_LIMIT_ANSWER

      response = llm.create_chat_completion(
        messages=messages,
        tools=[tool.schema() for tool in self.tools],
        max_tokens=self.max_tokens,
      )

      self._accumulate_usage(response.get("usage"))

      message = response["choices"][0]["message"]
      messages.append(message)

      content = message.get("content", "")

      # Preferred: native structured tool calls. Fallback for Qwen3 GGUF:
      # parse "<tool_call>" XML blocks from the raw text.
      tool_calls = message.get("tool_calls") or parse_tool_calls(content)

      if not tool_calls:
        return clean_answer(content)

      try:
        self._execute_tool_calls(tool_calls, messages, called)
      except _ToolLoopAbort:
        return LOOP_LIMIT_ANSWER

  def run_stream(self, messages: list[dict]) -> Generator[str, None, None]:
    """Streaming variant of ``run``.

    Yields the assistant's answer in token chunks instead of returning it all
    at once. Tool-calling rounds emit nothing: the Qwen3 thinking preamble and
    any ``<tool_call>`` blocks are hidden, and tool results feed back into the
    loop until the model produces a plain-text answer.

    Two output modes:

    - Preamble marker set (e.g. Qwen3 template): text is yielded only after
      the ``response`` marker line, so thinking is stripped while streaming.
    - No marker (online/plain-text providers): text is streamed verbatim.
    """
    llm = get_llm()

    messages = list(messages)
    self.tool_results = []

    called: dict = {}
    steps = 0
    max_steps = _max_agent_steps()

    while True:
      steps += 1
      if steps > max_steps:
        yield LOOP_LIMIT_ANSWER
        return

      stream = llm.create_chat_completion(
        messages=messages,
        tools=[tool.schema() for tool in self.tools],
        max_tokens=self.max_tokens,
        stream=True,
      )

      content = ""
      answer_started = False
      streamed_live = False
      native_calls: list[dict] = []

      for chunk in stream:
        # Usage-only chunks (no choices) carry token counts and nothing to
        # display — accumulate them and move on.
        if "choices" not in chunk:
          self._accumulate_usage(chunk.get("usage"))
          continue

        delta = chunk["choices"][0]["delta"]
        piece = delta.get("content") or ""

        if piece:
          content += piece

          if llm.stream_marker is None:
            # Plain provider (no thinking preamble): stream verbatim.
            streamed_live = True
            yield piece
          elif not answer_started:
            # Only show text that comes AFTER the "response" marker
            # (i.e. the actual answer, not the thinking preamble).
            marker = RESPONSE_MARKER_RE.search(content)
            if marker:
              answer_started = True
              tail = content[marker.end():]
              if tail:
                yield tail
          else:
            yield piece

        for call in delta.get("tool_calls") or []:
          native_calls.append(call)

      assistant = {"role": "assistant", "content": content}
      if native_calls:
        assistant["tool_calls"] = native_calls
      messages.append(assistant)

      tool_calls = native_calls or parse_tool_calls(content)

      if not tool_calls:
        # Final round. Content already streamed live needs no fallback; only
        # when nothing was emitted (e.g. a thinking preamble never reached the
        # marker) do we fall back to chunked emission of the clean answer.
        if not answer_started and not streamed_live:
          answer = clean_answer(content)
          step = 40
          for i in range(0, len(answer), step):
            yield answer[i:i + step]
        return

      try:
        self._execute_tool_calls(tool_calls, messages, called)
      except _ToolLoopAbort:
        yield LOOP_LIMIT_ANSWER
        return
