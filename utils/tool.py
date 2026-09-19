from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


def tool_error(code: str, message: str, retryable: bool = False) -> dict:
  """Structured error envelope so the model can decide whether to retry."""
  return {
    "ok": False,
    "error": {
      "code": code,
      "message": message,
      "retryable": retryable,
    },
  }


class Tool(ABC):

  name: str
  description: str
  input_model: type[BaseModel]

  @abstractmethod
  def execute(self, arguments: dict[str, Any]) -> Any:
    raise NotImplementedError("Subclasses must implement this method")

  def schema(self):
    return {
        "type": "function",
        "function": {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_model.model_json_schema(),
        },
    }

  def call(self, arguments: dict[str, Any]):
    validated = self.input_model.model_validate(arguments)

    return self.execute(
        validated.model_dump()
    )
