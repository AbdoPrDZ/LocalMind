import json
import re
from typing import Optional
from collections.abc import Generator

from utils.llm import get_llm
from utils.tool import Tool

TOOL_CALL_RE = re.compile(
  r"<tool_call>(.*?)</tool_call>",
  re.DOTALL,
)

# Qwen3 emits " thinking\n...\nresponse\n<reply>": the answer separator is a
# "response" marker line (spaces tolerated), everything before it is thinking.
RESPONSE_MARKER_RE = re.compile(r"^\s*response\s*$\n?", re.MULTILINE)


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
    """Remove Qwen3 thinking/reasoning blocks and return the final answer."""

    content = (content or "").strip()

    # Remove <think>...</think> blocks.
    # DOTALL allows the thinking section to span multiple lines.
    content = re.sub(
        r"<think>.*?</think>",
        "",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Fallback for models/templates that use a `response` marker.
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

  def run(self, messages: list[dict]) -> str:
    """Run the tool-loop against a full message history.

    ``messages`` is a chat-completion list that already includes the system
    prompt and all prior turns (see ``apps.base.Chat.send``). The loop appends
    the assistant's choices and tool results as it iterates, so the model sees
    its own previous calls on every step.
    """
    llm = get_llm()

    while True:
      response = llm.create_chat_completion(
        messages=messages,
        tools=[tool.schema() for tool in self.tools],
        max_tokens=self.max_tokens,
      )

      message = response["choices"][0]["message"]
      messages.append(message)

      content = message.get("content", "")

      # Preferred: native structured tool calls. Fallback for Qwen3 GGUF:
      # parse "<tool_call>" XML blocks from the raw text.
      tool_calls = message.get("tool_calls") or parse_tool_calls(content)

      if not tool_calls:
        return clean_answer(content)

      for call in tool_calls:
        fn = call.get("function", call)

        name = fn.get("name", "")

        arguments = fn.get("arguments", {})
        if isinstance(arguments, str):
          try:
            arguments = json.loads(arguments)
          except json.JSONDecodeError:
            arguments = {}

        tool = self.tool_map.get(name)

        if tool is None:
          result = {"error": f"Unknown tool: {name}"}
        else:
          result = tool.call(arguments)

        # Qwen3 template expects tool results wrapped in a "tool" message;
        # it renders them as <tool_response> blocks automatically.
        messages.append({
          "role": "tool",
          "content": json.dumps(result),
        })

  def run_stream(self, messages: list[dict]) -> Generator[str, None, None]:
    """Streaming variant of ``run``.

    Yields the assistant's answer in token chunks instead of returning it all
    at once. Tool-calling rounds emit nothing: the Qwen3 thinking preamble and
    any ``<tool_call>`` blocks are hidden, and tool results feed back into the
    loop until the model produces a plain-text answer.

    Two output modes:

    - Qwen3 template (marker present): text is yielded only after the
      ``response`` marker line, so thinking is stripped while streaming.
    - No marker (plain text model): the cleaned answer is emitted in chunks.
    """
    llm = get_llm()

    messages = list(messages)

    while True:
      stream = llm.create_chat_completion(
        messages=messages,
        tools=[tool.schema() for tool in self.tools],
        max_tokens=self.max_tokens,
        stream=True,
      )

      content = ""
      answer_started = False
      native_calls: list[dict] = []

      for chunk in stream:
        delta = chunk["choices"][0]["delta"]
        piece = delta.get("content") or ""

        if piece:
          content += piece

          # Only show text that comes AFTER the "response" marker
          # (i.e. the actual answer, not the thinking preamble).
          if not answer_started:
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

      messages.append({"role": "assistant", "content": content})

      tool_calls = native_calls or parse_tool_calls(content)

      if not tool_calls:
        # Final round. If the model used no thinking marker, the loop above
        # yielded nothing, so fall back to chunked emission of the clean answer.
        if not answer_started:
          answer = clean_answer(content)
          step = 40
          for i in range(0, len(answer), step):
            yield answer[i:i + step]
        return

      for call in tool_calls:
        fn = call.get("function", call)

        name = fn.get("name", "")

        arguments = fn.get("arguments", {})
        if isinstance(arguments, str):
          try:
            arguments = json.loads(arguments)
          except json.JSONDecodeError:
            arguments = {}

        tool = self.tool_map.get(name)

        if tool is None:
          result = {"error": f"Unknown tool: {name}"}
        else:
          result = tool.call(arguments)

        messages.append({
          "role": "tool",
          "content": json.dumps(result),
        })
