from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


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
