"""Filesystem tools scoped to the configured ALLOWED_PLACES folders.

File access is parameterized by "place": a named folder from the
``ALLOWED_PLACES`` env var (comma-separated ``name=path`` entries, default
``workspace=./workspace``). Every read/write/list is resolved against one of
those allowed folders and rejected when it would escape them — the LLM can
never touch anything outside the configured roots.
"""

import glob
import os
from typing import Any, Literal, Type

from pydantic import BaseModel, Field, create_model

from utils.env import ENV
from utils.tool import Tool

DEFAULT_ALLOWED_PLACES = "workspace=./workspace"

_MAX_WRITE_CHARS = 200_000
_MAX_LIST_ENTRIES = 500


def _parse_allowed_places(raw: str | None) -> dict[str, str]:
  """Parse ``name=path[,name=path]`` into a name → configured-path dict."""
  entries = (raw or "").split(",")
  places: dict[str, str] = {}
  for entry in entries:
    entry = entry.strip()
    if not entry:
      continue
    if "=" in entry:
      name, path = entry.split("=", 1)
    else:
      name = os.path.basename(os.path.normpath(entry)) or "workspace"
      path = entry
    name = name.strip()
    path = path.strip()
    if name and path:
      places[name] = path
  return places


def resolve_allowed_places() -> dict[str, str]:
  """Resolve ALLOWED_PLACES to absolute ``{name: realpath}`` without creating them."""
  raw = ENV.get("ALLOWED_PLACES", default=DEFAULT_ALLOWED_PLACES)
  parsed = _parse_allowed_places(raw)
  if not parsed:
    parsed = _parse_allowed_places(DEFAULT_ALLOWED_PLACES)
  return {name: os.path.realpath(path) for name, path in parsed.items()}


def _roots() -> list[str]:
  return list(resolve_allowed_places().values())


def _contained(target: str, roots: list[str]) -> bool:
  """True when ``target`` (realpath) is inside one of ``roots`` (realpaths)."""
  try:
    return any(
      os.path.commonpath([root, target]) == root
      for root in roots
      if os.path.exists(root)
    )
  except ValueError:
    return False


def _validate_place(place: str) -> str:
  places = resolve_allowed_places()
  if place not in places:
    choices = ", ".join(sorted(places))
    raise ValueError(f"Unknown place '{place}'. Allowed places: {choices}.")
  root = places[place]
  if not os.path.isdir(root):
    os.makedirs(root, exist_ok=True)
  return root


def _resolve_path(place: str, path: str) -> tuple[str, str]:
  """Map ``path`` onto its place root; return (realpath, root)."""
  root = _validate_place(place)
  target = os.path.realpath(os.path.join(root, path))
  if not _contained(target, _roots()):
    raise ValueError(f"Path '{path}' escapes the allowed folders.")
  return target, root


def _build_input(description: str, fields: dict, places: list[str]) -> Type[BaseModel]:
  return create_model(
    "FilesInput",
    place=(
      Literal[tuple(places)],
      Field(
        default=places[0],
        description=(
          "Which allowed folder to use. "
          f"Allowed folders: {', '.join(sorted(places))}."
        ),
      ),
    ),
    **fields,
  )


class ReadFileTool(Tool):
  name = "read_file"
  description = (
    "Read a text file from one of the allowed folders and return its contents. "
    "Use `path` relative to that folder."
  )

  def __init__(self, input_model: Type[BaseModel]) -> None:
    self.input_model = input_model

  def execute(self, arguments: dict[str, Any]) -> Any:
    try:
      target, _ = _resolve_path(arguments["place"], arguments["path"])
    except ValueError as exc:
      return {"error": str(exc)}
    if not os.path.isfile(target):
      return {"error": f"No such file: {arguments['place']}/{arguments['path']}"}

    with open(target, "r", encoding="utf-8", errors="replace") as fh:
      content = fh.read()
    return {"file": f"{arguments['place']}/{arguments['path']}", "content": content, "bytes": len(content)}


class WriteFileTool(Tool):
  name = "write_file"
  description = (
    "Create or append to a text file inside one of the allowed folders, "
    "creating missing directories as needed. Use `path` relative to that folder."
  )

  def __init__(self, input_model: Type[BaseModel]) -> None:
    self.input_model = input_model

  def execute(self, arguments: dict[str, Any]) -> Any:
    content = arguments.get("content") or ""
    if len(content) > _MAX_WRITE_CHARS:
      return {"error": f"Content too large (max {_MAX_WRITE_CHARS} characters)."}

    try:
      target, _ = _resolve_path(arguments["place"], arguments["path"])
      os.makedirs(os.path.dirname(target), exist_ok=True)
    except ValueError as exc:
      return {"error": str(exc)}

    mode = "a" if arguments.get("append") else "w"
    with open(target, mode, encoding="utf-8") as fh:
      fh.write(content)

    return {
      "success": True,
      "file": f"{arguments['place']}/{arguments['path']}",
      "bytes": os.path.getsize(target),
      "appended": arguments.get("append", False),
    }


class ListDirTool(Tool):
  name = "list_dir"
  description = (
    "List files and folders inside one of the allowed folders. "
    "`path` is relative to that folder, `pattern` a glob (e.g. *.md), "
    "`recursive` walks subfolders."
  )

  def __init__(self, input_model: Type[BaseModel]) -> None:
    self.input_model = input_model

  def execute(self, arguments: dict[str, Any]) -> Any:
    try:
      target, root = _resolve_path(arguments["place"], arguments["path"])
    except ValueError as exc:
      return {"error": str(exc)}
    if not os.path.isdir(target):
      return {"error": f"Not a folder: {arguments['place']}/{arguments['path']}"}

    pattern = arguments.get("pattern") or "*"
    if arguments.get("recursive"):
      pattern = os.path.join("**", pattern)
      matches = sorted(glob.glob(os.path.join(target, pattern), recursive=True))
    else:
      matches = sorted(glob.glob(os.path.join(target, pattern)))

    entries: list[dict] = []
    seen: set[str] = set()
    for match in matches[: _MAX_LIST_ENTRIES]:
      real = os.path.realpath(match)
      if real in seen:
        continue
      seen.add(real)
      entries.append({
        "name": os.path.relpath(match, root).replace(os.sep, "/"),
        "type": "folder" if os.path.isdir(match) else "file",
        "bytes": os.path.getsize(match) if os.path.isfile(match) else 0,
      })

    return {
      "place": arguments["place"],
      "folder": arguments["path"],
      "count": len(entries),
      "entries": entries,
    }


def build_files_tools() -> list[Tool]:
  """Instantiate the file tools parameterized by the allowed folders."""
  places = sorted(resolve_allowed_places())
  if not places:
    places = ["workspace"]

  path_field = (
    str,
    Field(description="Path to the file, relative to the chosen folder."),
  )
  return [
    ReadFileTool(_build_input(
      "input",
      {"path": path_field},
      places,
    )),
    WriteFileTool(_build_input(
      "input",
      {
        "path": path_field,
        "content": (str, Field(description="Text content to write.")),
        "append": (bool, Field(default=False, description="Append instead of overwrite.")),
      },
      places,
    )),
    ListDirTool(_build_input(
      "input",
      {
        "path": (str, Field(default=".", description="Folder to list, relative to the place.")),
        "pattern": (str, Field(default="*", description="Glob pattern to match.")),
        "recursive": (bool, Field(default=False, description="Recurse into subfolders.")),
      },
      places,
    )),
  ]