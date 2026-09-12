"""Gemini provider (online) via the ``google-genai`` SDK.

Speaks OpenAI-style chat completions to the ``Agent``: messages and tool
schemas are translated into Gemini contents/function declarations on the way
in, and the response (text parts + function-call parts) is normalized back
into OpenAI shape on the way out.
"""

import json
from typing import Any, Iterator, Optional

from utils.env import ENV
from utils.providers.base import LLMProvider

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"

#: OpenAPI-3-style keys Gemini's FunctionDeclaration accepts. Pydantic's JSON
#: schema emits extra keys (``title``, ``$defs``, ``$ref``) that must be dropped.
_OPENAPI_KEYS = {
  "anyOf",
  "allOf",
  "additionalProperties",
  "default",
  "description",
  "enum",
  "examples",
  "format",
  "items",
  "maximum",
  "maxItems",
  "maxLength",
  "minimum",
  "minItems",
  "minLength",
  "pattern",
  "properties",
  "required",
  "type",
}


def openai_schema_to_gemini(schema: dict) -> dict:
  """Convert an OpenAI function ``parameters`` JSON schema to an OpenAPI 3 schema.

  Resolves ``$ref``/``$defs`` inline (Gemini does not accept ``$id``-based
  references here) and keeps only the keys Gemini understands.
  """
  defs = schema.get("$defs") or {}
  converted = _convert(schema, defs)

  if not converted:
    return {"type": "object", "properties": {}}
  return converted


def _convert(node: Any, defs: dict) -> Any:
  if isinstance(node, dict):
    ref = node.get("$ref")
    if ref:
      node = defs.get(ref.rsplit("/", 1)[-1]) or {}

    result = {}
    for key in _OPENAPI_KEYS:
      if key in node and key != "properties":
        result[key] = _convert(node[key], defs)

    if "properties" in node:
      result["properties"] = {
        name: _convert(sub, defs)
        for name, sub in (node["properties"] or {}).items()
      }
    return result

  if isinstance(node, list):
    return [_convert(item, defs) for item in node]
  return node


def _parse_arguments(arguments: Any) -> dict:
  if isinstance(arguments, str):
    try:
      return json.loads(arguments)
    except json.JSONDecodeError:
      return {}
  return arguments or {}


class GeminiLLMProvider(LLMProvider):

  # Gemini has no thinking preamble in the chat payload — stream verbatim.
  stream_marker = None

  def __init__(self) -> None:
    from google import genai
    from google.genai import types

    self._types = types
    self._client = genai.Client(api_key=ENV.get_gemini_api_key())
    self.model = ENV.get("GEMINI_MODEL", default=DEFAULT_GEMINI_MODEL)

  def _config(self, tools: Optional[list[dict]], max_tokens: int, system_instruction: str):
    types = self._types

    function_declarations = []
    for schema in tools or []:
      function = schema.get("function", {}) if isinstance(schema, dict) else {}
      function_declarations.append(types.FunctionDeclaration(
        name=function.get("name", ""),
        description=function.get("description", ""),
        parameters=openai_schema_to_gemini(function.get("parameters") or {}),
      ))

    gen_tools = None
    if function_declarations:
      gen_tools = [types.Tool(function_declarations=function_declarations)]

    return types.GenerateContentConfig(
      system_instruction=system_instruction.strip() or None,
      tools=gen_tools,
      max_output_tokens=max_tokens,
    )

  def _to_contents(self, messages: list[dict]) -> list:
    """Translate an OpenAI-style message list into Gemini ``Content`` objects.

    - ``system`` → captured as ``system_instruction`` (returned separately).
    - ``assistant`` → a ``model`` content; any ``tool_calls`` become
      ``functionCall`` parts.
    - ``tool`` → a ``user`` content with a ``functionResponse`` part (paired by
      name + order with the preceding function calls).
    """
    types = self._types
    contents = []
    system_instruction = ""

    for message in messages:
      role = message.get("role")

      if role == "system":
        system_instruction = message.get("content") or ""
        continue

      if role == "tool":
        payload = json.loads(message.get("content") or "{}") if message.get("content") else {}
        # Gemini's FunctionResponse requires a JSON object payload; wrap lists
        # and primitives (e.g. tool results that are arrays) in an object.
        if not isinstance(payload, dict):
          payload = {"result": payload}
        parts = [types.Part(function_response=types.FunctionResponse(
          id=message.get("tool_call_id"),
          name=message.get("name") or "",
          response=payload,
        ))]
        contents.append(types.Content(role="user", parts=parts))
        continue

      parts = []
      content = message.get("content")
      if content:
        parts.append(types.Part(text=content))

      for call in message.get("tool_calls") or []:
        function = call.get("function", call) if isinstance(call, dict) else {}
        # Pass the id and thought_signature back so Gemini can pair this
        # call with the corresponding function response on the next turn.
        parts.append(types.Part(
          function_call=types.FunctionCall(
            id=call.get("id"),
            name=function.get("name", ""),
            args=_parse_arguments(function.get("arguments")),
          ),
          thought_signature=call.get("thought_signature"),
        ))

      contents.append(types.Content(
        role="model" if role == "assistant" else "user",
        parts=parts,
      ))

    return contents, system_instruction

  @staticmethod
  def _openai_message(parts) -> dict:
    """Normalize Gemini response parts into an OpenAI-style assistant message.

    Function-call parts keep the Gemini ``id`` and the part-level
    ``thought_signature`` hidden in OpenAI-style entries so a later turn can
    send them back verbatim (Gemini 3.x requires ``thought_signature``).
    """
    text = "".join(part.text or "" for part in parts if part.text)
    tool_calls = []
    for index, part in enumerate(parts):
      call = getattr(part, "function_call", None)
      if call is None:
        continue
      entry = {
        "id": call.id or f"call_{index}",
        "type": "function",
        "function": {
          "name": call.name,
          "arguments": json.dumps(call.args or {}),
        },
      }
      signature = getattr(part, "thought_signature", None)
      if signature:
        entry["thought_signature"] = signature
      tool_calls.append(entry)

    message = {"role": "assistant", "content": text}
    if tool_calls:
      message["tool_calls"] = tool_calls
    return message

  @staticmethod
  def _usage_dict(usage_metadata) -> dict | None:
    """Normalize Gemini ``usage_metadata`` into OpenAI-style usage counts.

    Streamed chunks may carry usage with only some of the fields populated,
    so every count is read defensively.
    """
    if usage_metadata is None:
      return None
    prompt = int(getattr(usage_metadata, "prompt_token_count", None) or 0)
    completion = int(getattr(usage_metadata, "response_token_count", None) or 0)
    total = int(getattr(usage_metadata, "total_token_count", None) or 0)
    if total <= 0:
      total = prompt + completion
    if prompt == 0 and completion == 0:
      return None
    return {
      "prompt_tokens": prompt,
      "completion_tokens": completion,
      "total_tokens": total,
    }

  def _chat(self, messages: list[dict], tools: Optional[list[dict]], max_tokens: int) -> dict:
    contents, system_instruction = self._to_contents(messages)

    response = self._client.models.generate_content(
      model=self.model,
      contents=contents,
      config=self._config(tools, max_tokens, system_instruction),
    )

    parts = []
    if response.candidates and response.candidates[0].content:
      parts = response.candidates[0].content.parts or []

    result = {"choices": [{"message": self._openai_message(parts)}]}
    usage = self._usage_dict(response.usage_metadata)
    if usage:
      result["usage"] = usage
    return result

  def _stream(
    self,
    messages: list[dict],
    tools: Optional[list[dict]],
    max_tokens: int,
  ) -> Iterator[dict]:
    contents, system_instruction = self._to_contents(messages)

    stream = self._client.models.generate_content_stream(
      model=self.model,
      contents=contents,
      config=self._config(tools, max_tokens, system_instruction),
    )

    pending_calls = []
    last_usage = None
    for chunk in stream:
      parts = []
      if chunk.candidates and chunk.candidates[0].content:
        parts = chunk.candidates[0].content.parts or []

      usage = self._usage_dict(chunk.usage_metadata)
      if usage:
        last_usage = usage

      has_function_call = False
      for part in parts:
        call = getattr(part, "function_call", None)
        if call is None:
          continue
        has_function_call = True
        entry = {
          "id": call.id or f"call_{len(pending_calls)}",
          "type": "function",
          "function": {
            "name": call.name,
            "arguments": json.dumps(call.args or {}),
          },
        }
        signature = getattr(part, "thought_signature", None)
        if signature:
          entry["thought_signature"] = signature
        pending_calls.append(entry)

      if not has_function_call:
        # Reading .text on a function-call chunk makes the SDK print warnings.
        text = chunk.text or ""
        if text:
          yield {"choices": [{"delta": {"content": text}}]}

    if last_usage:
      yield {"usage": last_usage}
    if pending_calls:
      yield {"choices": [{"delta": {"tool_calls": pending_calls}}]}

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