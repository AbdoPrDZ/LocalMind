from typing import Dict, Type

from utils.model import BaseModel

CRUD_OPERATIONS = ("create", "get", "list", "update", "delete")

MODELS: Dict[str, Type[BaseModel]] = {}


def register_model(**enabled: bool):
  """Class decorator that registers an ORM model by its table name.

  CRUD operations are enabled by default. Pass ``False`` to disable one
  or more of them, e.g.::

      @register_model(delete=False)                    # no delete
      @register_model(delete=False, update=False)      # no delete, no update
      @register_model(create=False, update=False)      # read-only

  The enabled operations are stored on the class as ``crud_operations``
  and used by ``build_crud_tools`` to decide which tools to create.
  """
  unknown = set(enabled) - set(CRUD_OPERATIONS)
  if unknown:
    raise ValueError(
      f"Unknown CRUD operations: {sorted(unknown)}. "
      f"Valid operations: {list(CRUD_OPERATIONS)}."
    )

  def decorator(cls: Type[BaseModel]) -> Type[BaseModel]:
    cls.crud_operations = frozenset(
      operation for operation in CRUD_OPERATIONS
      if enabled.get(operation, True)
    )

    MODELS[cls.__tablename__] = cls
    return cls

  return decorator


def get_model(name: str) -> Type[BaseModel]:
  try:
    return MODELS[name]
  except KeyError:
    raise ValueError(
      f"Unknown model '{name}'. Registered: {list(MODELS)}"
    ) from None
