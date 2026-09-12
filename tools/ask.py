"""Generic tool that asks the user a question and waits for an answer.

The tool itself is UI-agnostic: each app (cmd, web, api, desktop) injects its
own question handler — a blocking callable ``(question, options,
allow_free_text) -> answer | None`` — when building the agent. Without a
handler the tool reports an error to the model instead of crashing.
"""

from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from utils.tool import Tool

#: ``question`` text, optional pre-set choices, whether a free-text reply is
#: allowed. Returns the user's answer or ``None`` when they dismiss/abort.
QuestionHandler = Callable[[str, Optional[list[str]], bool], Optional[str]]


class _AskUserInput(BaseModel):
  question: str = Field(
    description="The plain question to ask the user, complete and self-contained."
  )
  options: Optional[list[str]] = Field(
    default=None,
    description="Optional list of choices; when present the user picks one of them.",
  )
  allow_free_text: bool = Field(
    default=True,
    description="Whether the user may type their own answer instead of a choice.",
  )


class AskUserTool(Tool):
  name = "ask_user"
  description = (
    "Ask the user a clarifying question and wait for their answer. Use it when "
    "you cannot proceed without missing information, the request is ambiguous, "
    "or the user must confirm an action or make a choice."
  )

  def __init__(self, handler: Optional[QuestionHandler] = None) -> None:
    self.handler = handler
    self.input_model = _AskUserInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    if self.handler is None:
      return {
        "error": (
          "No question handler is configured for this interface — the user "
          "cannot be asked. State the missing information and what you need."
        )
      }

    answer = self.handler(
      arguments["question"],
      options=arguments.get("options"),
      allow_free_text=arguments.get("allow_free_text", True),
    )

    if answer is None or not str(answer).strip():
      return {"error": "The user dismissed or left the question unanswered."}
    return {"answer": str(answer).strip()}


def build_ask_tools(handler: Optional[QuestionHandler] = None) -> list[Tool]:
  """Instantiate the ask-user tool bound to an app-provided handler."""
  return [AskUserTool(handler)]