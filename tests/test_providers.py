import json
import os

import pytest
from pydantic import BaseModel

os.environ["LLM_PROVIDER"] = "gemini"
os.environ["GEMINI_API_KEY"] = "test-key"

from utils.providers.gemini import (  # noqa: E402
  GeminiLLMProvider,
  openai_schema_to_gemini,
)


# ---------------------------------------------------------------------------
# Schema conversion (pure helper)
# ---------------------------------------------------------------------------


def test_schema_drops_openai_only_keys():
  schema = {
    "$defs": {"Item": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "title": "Item"}},
    "type": "object",
    "title": "Input",
    "properties": {
      "items": {"$ref": "#/$defs/Item"},
      "count": {"type": "integer", "title": "Count"},
    },
    "required": ["items"],
  }

  converted = openai_schema_to_gemini(schema)

  assert converted["type"] == "object"
  assert converted["required"] == ["items"]
  assert "title" not in converted
  assert "$defs" not in converted
  assert converted["properties"]["items"]["type"] == "object"
  assert converted["properties"]["items"]["properties"]["name"]["type"] == "string"
  assert "title" not in converted["properties"]["items"]


def test_schema_keeps_openapi_keys_and_anyOf():
  schema = {
    "type": "object",
    "properties": {
      "val": {
        "anyOf": [{"type": "string"}, {"type": "integer"}],
        "description": "anything",
      }
    },
  }

  converted = openai_schema_to_gemini(schema)

  assert converted["properties"]["val"]["anyOf"] == [
    {"type": "string"},
    {"type": "integer"},
  ]
  assert converted["properties"]["val"]["description"] == "anything"


def test_schema_falls_back_for_empty():
  assert openai_schema_to_gemini({}) == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# Gemini message translation
# ---------------------------------------------------------------------------


@pytest.fixture()
def provider():
  return GeminiLLMProvider()


def _dump(contents):
  return [json.loads(content.model_dump_json()) for content in contents]


def test_to_contents_separates_system_and_maps_roles(provider):
  contents, system_instruction = provider._to_contents([
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "hi there"},
  ])

  assert system_instruction == "You are helpful."
  dumped = _dump(contents)

  assert dumped[0]["role"] == "user"
  assert dumped[0]["parts"][0]["text"] == "hello"
  assert dumped[1]["role"] == "model"
  assert dumped[1]["parts"][0]["text"] == "hi there"


def test_to_contents_maps_tool_call_round_trip(provider):
  messages = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "list projects"},
    {
      "role": "assistant",
      "content": "",
      "tool_calls": [
        {
          "id": "call_0",
          "type": "function",
          "function": {"name": "list_projects", "arguments": json.dumps({"limit": 2})},
        }
      ],
    },
    {"role": "tool", "name": "list_projects", "tool_call_id": "call_0", "content": json.dumps([{"id": 1}])},
  ]

  contents, _ = provider._to_contents(messages)
  dumped = _dump(contents)

  # messages = [system(skipped), user, assistant(tool_calls), tool]
  assert len(dumped) == 3
  assert dumped[0]["role"] == "user"
  assert dumped[0]["parts"][0]["text"] == "list projects"

  assistant = dumped[1]
  assert assistant["role"] == "model"
  call = assistant["parts"][0]["function_call"]
  assert call["name"] == "list_projects"
  assert call["args"] == {"limit": 2}
  assert call["id"] == "call_0"

  tool = dumped[2]
  assert tool["role"] == "user"
  response = tool["parts"][0]["function_response"]
  assert response["name"] == "list_projects"
  assert response["id"] == "call_0"
  assert response["response"] == {"result": [{"id": 1}]}


def test_openai_message_normalization(provider):
  from google.genai import types

  parts = [
    types.Part(text="First"),
    types.Part(text=" second"),
    types.Part(function_call=types.FunctionCall(
      id="call_42", name="list_projects", args={"limit": 1}
    )),
  ]

  message = provider._openai_message(parts)

  assert message["role"] == "assistant"
  assert message["content"] == "First second"
  assert len(message["tool_calls"]) == 1
  call = message["tool_calls"][0]
  assert call["id"] == "call_42"
  assert call["function"]["name"] == "list_projects"
  assert json.loads(call["function"]["arguments"]) == {"limit": 1}


def test_openai_message_carries_thought_signature(provider):
  from google.genai import types

  parts = [types.Part(
    function_call=types.FunctionCall(name="do_thing", args={}),
    thought_signature=b"\x01\x02secret",
  )]

  message = provider._openai_message(parts)
  call = message["tool_calls"][0]

  assert call["thought_signature"] == b"\x01\x02secret"

  # And the signature round-trips back into a Part.
  contents, _ = provider._to_contents([
    {"role": "user", "content": "go"},
    {"role": "assistant", "content": "", "tool_calls": [call]},
  ])
  dumped = _dump(contents)
  part = dumped[1]["parts"][0]
  assert part["function_call"]["id"] == call["id"]
  # The SDK serializes the bytes signature as base64 in JSON.
  assert part["thought_signature"] == "AQJzZWNyZXQ="


def test_config_builds_function_declarations(provider):
  config = provider._config(
    tools=[{
      "type": "function",
      "function": {
        "name": "list_projects",
        "description": "Lists projects.",
        "parameters": {"type": "object", "properties": {}, "title": "In"},
      },
    }],
    max_tokens=256,
    system_instruction="be brief",
  )

  dumped = json.loads(config.model_dump_json(exclude_none=True))

  assert dumped["system_instruction"] == "be brief"
  assert dumped["max_output_tokens"] == 256
  declarations = dumped["tools"][0]["function_declarations"]
  assert declarations[0]["name"] == "list_projects"
  assert "title" not in declarations[0]["parameters"]