"""Generic CRUD tools: one class per operation, parameterized by any model.

No tool is hardcoded to a specific model. Every model registered in the
registry gets a full set of create/get/list/update/delete tool instances
(create_project, list_tasks, ...) built by build_crud_tools().
"""

from datetime import date, datetime
from typing import Any, Optional as Opt, Type

from pydantic import BaseModel, Field, create_model
from sqlalchemy import select
from sqlalchemy.types import (
  BigInteger,
  Boolean,
  Date,
  DateTime,
  Float,
  Integer,
  Numeric,
  SmallInteger,
  String,
  Text,
  Unicode,
  UnicodeText,
)

from database import get_session
from utils.registry import CRUD_OPERATIONS, MODELS
from utils.model import BaseModel as ORMModel
from utils.tool import Tool


# --------------------------------------------------------------------------
# Pydantic schema helpers
# --------------------------------------------------------------------------

_INT_TYPES = (SmallInteger, Integer, BigInteger)
_FLOAT_TYPES = (Float, Numeric)
_STR_TYPES = (String, Text, Unicode, UnicodeText)
_DT_TYPES = (DateTime, Date)


def _column_python_type(col) -> type:
  ctype = col.type
  if isinstance(ctype, _DT_TYPES):
    return datetime
  if isinstance(ctype, Boolean):
    return bool
  if isinstance(ctype, _INT_TYPES):
    return int
  if isinstance(ctype, _FLOAT_TYPES):
    return float
  return str


def build_schema(
  model: Type[ORMModel],
  *,
  only_id: bool = False,
  include_id: bool = False,
  all_optional: bool = False,
) -> Type[BaseModel]:
  """Dynamically generate a Pydantic input schema from an ORM model's columns."""
  fields: dict[str, Any] = {}

  if only_id:
    fields["record_id"] = (
      int,
      Field(description=f"Primary key of a {model.__description__}."),
    )
    return create_model(f"{model.__name__}ById", **fields)

  for col in model.__table__.columns:
    if col.primary_key:
      # Primary keys are identified via the dedicated `record_id` field,
      # never as editable columns.
      continue

    py_type = _column_python_type(col)

    is_optional = (
      all_optional
      or col.nullable
      or col.default is not None
      or col.server_default is not None
    )

    if is_optional:
      fields[col.name] = (Opt[py_type], Field(default=None, description=col.name))
    else:
      fields[col.name] = (py_type, Field(description=col.name))

  if include_id:
    fields["record_id"] = (
      int,
      Field(description=f"Primary key of a {model.__description__}."),
    )

  return create_model(f"{model.__name__}Input", **fields)


def _json_safe(value: Any) -> Any:
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  return value


def _to_dict(obj: ORMModel) -> dict:
  return {
    col.name: _json_safe(getattr(obj, col.name))
    for col in obj.__table__.columns
  }


# --------------------------------------------------------------------------
# Generic CRUD tools
# --------------------------------------------------------------------------


class ModelTool(Tool):
  """Base for all generic model tools. Instances are created per model."""

  operation: str = ""

  def __init__(self, model: Type[ORMModel]) -> None:
    self.model = model
    self.name = f"{self.operation}_{model.__tablename__}"
    self.input_model = self._make_input()

  @property
  def _noun(self) -> str:
    return (self.model.__description__ or self.model.__tablename__).lower()

  def _make_input(self) -> Type[BaseModel]:
    raise NotImplementedError


class CreateModelTool(ModelTool):
  operation = "create"

  @property
  def description(self) -> str:
    return f"Create a new {self._noun}. Values go in the matching field names."

  def _make_input(self) -> Type[BaseModel]:
    return build_schema(self.model)

  def execute(self, arguments: dict[str, Any]) -> Any:
    session = get_session()
    try:
      # Drop explicit Nones so column defaults still apply.
      data = {key: value for key, value in arguments.items() if value is not None}

      obj = self.model(**data)
      session.add(obj)
      session.commit()
      return {"success": True, "row": _to_dict(obj)}
    except Exception as e:
      session.rollback()
      return {"success": False, "error": str(e)}
    finally:
      session.close()


class GetModelTool(ModelTool):
  operation = "get"

  @property
  def description(self) -> str:
    return f"Get a single {self._noun} by record_id."

  def _make_input(self) -> Type[BaseModel]:
    return build_schema(self.model, only_id=True)

  def execute(self, arguments: dict[str, Any]) -> Any:
    session = get_session()
    try:
      obj = session.get(self.model, arguments["record_id"])
      if obj is None:
        return {"error": f"{self.model.__name__} not found"}
      return _to_dict(obj)
    finally:
      session.close()


class ListModelTool(ModelTool):
  operation = "list"

  @property
  def description(self) -> str:
    return (
      f"List up to `limit` rows of {self._noun}s, "
      f"skipping `offset` rows first."
    )

  def _make_input(self) -> Type[BaseModel]:
    return _ListInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    session = get_session()
    try:
      rows = session.scalars(
        select(self.model)
        .offset(arguments.get("offset", 0))
        .limit(arguments.get("limit", 10))
      ).all()
      return [_to_dict(row) for row in rows]
    finally:
      session.close()


class UpdateModelTool(ModelTool):
  operation = "update"

  @property
  def description(self) -> str:
    return (
      f"Update an existing {self._noun} by record_id. "
      f"Only supplied fields are changed; omit fields to leave them untouched."
    )

  def _make_input(self) -> Type[BaseModel]:
    return build_schema(self.model, include_id=True, all_optional=True)

  def execute(self, arguments: dict[str, Any]) -> Any:
    record_id = arguments.pop("record_id")

    session = get_session()
    try:
      obj = session.get(self.model, record_id)
      if obj is None:
        return {"error": f"{self.model.__name__} not found"}

      for key, value in arguments.items():
        if value is not None:
          setattr(obj, key, value)

      session.commit()
      return {"success": True, "row": _to_dict(obj)}
    except Exception as e:
      session.rollback()
      return {"success": False, "error": str(e)}
    finally:
      session.close()


class DeleteModelTool(ModelTool):
  operation = "delete"

  @property
  def description(self) -> str:
    return (
      f"Delete the {self._noun} with the given record_id. "
      f"Destructive: the row is permanently removed."
    )

  def _make_input(self) -> Type[BaseModel]:
    return build_schema(self.model, only_id=True)

  def execute(self, arguments: dict[str, Any]) -> Any:
    session = get_session()
    try:
      obj = session.get(self.model, arguments["record_id"])
      if obj is None:
        return {"error": f"{self.model.__name__} not found"}

      session.delete(obj)
      session.commit()
      return {"success": True, "deleted": arguments["record_id"]}
    except Exception as e:
      session.rollback()
      return {"success": False, "error": str(e)}
    finally:
      session.close()


class _ListInput(BaseModel):
  limit: int = Field(default=10, ge=1, le=100, description="Max rows to return.")
  offset: int = Field(default=0, ge=0, description="How many rows to skip.")


# --------------------------------------------------------------------------
# Registry → tool instances
# --------------------------------------------------------------------------


def build_crud_tools() -> list[Tool]:
  """Instantiate one generic CRUD tool set per registered model.

  Only operations enabled at registration time (via ``@register_model``)
  are created. Everything is enabled by default.
  """
  operation_tool = {
    "create": CreateModelTool,
    "get": GetModelTool,
    "list": ListModelTool,
    "update": UpdateModelTool,
    "delete": DeleteModelTool,
  }

  tools: list[Tool] = []

  for model in MODELS.values():
    enabled = getattr(model, "crud_operations", frozenset(CRUD_OPERATIONS))

    for operation in CRUD_OPERATIONS:
      if operation not in enabled:
        continue

      tools.append(operation_tool[operation](model))

  return tools
