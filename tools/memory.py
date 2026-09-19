"""LLM-facing tools over the global memory service.

The LLM reaches memories only through these controlled tools → ``MemoryService``
→ SQLAlchemy. It never touches the database directly.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from services.memory import MemoryService
from utils.tool import Tool, tool_error


class _SearchGlobalMemoryInput(BaseModel):
  query: str = Field(
    description="Search phrase to match against memory contents or types."
  )
  limit: int = Field(
    default=5,
    ge=1,
    le=50,
    description="Max memory entries to return.",
  )


class SearchGlobalMemoryTool(Tool):
  name = "search_global_memory"
  description = (
    "Search persistent global memory for facts, preferences, decisions, or topics "
    "learned in previous conversations. Use this before guessing whenever the "
    "current context may lack the answer."
  )
  input_model = _SearchGlobalMemoryInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    return MemoryService.search(arguments["query"], limit=arguments["limit"])


class _GetMemoryInput(BaseModel):
  memory_id: int = Field(description="Primary key of a global memory entry.")


class GetMemoryTool(Tool):
  name = "get_memory"
  description = "Load one specific global memory entry by its ID."
  input_model = _GetMemoryInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    memory = MemoryService.get(arguments["memory_id"])
    if memory is None:
      return tool_error("not_found", "Memory not found")
    return memory


class _GetChatContextInput(BaseModel):
  chat_id: int = Field(description="Primary key of the chat to inspect.")


class GetChatContextTool(Tool):
  name = "get_chat_context"
  description = (
    "Return the summarized context of another conversation by chat_id, without "
    "loading its full message history. Use it when a memory entry references a "
    "source chat and you need that conversation's context."
  )
  input_model = _GetChatContextInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    context = MemoryService.get_chat_context(arguments["chat_id"])
    if context is None:
      return tool_error("not_found", "Chat not found")
    return {"chat_id": arguments["chat_id"], "context": context}


class _SearchChatHistoryInput(BaseModel):
  query: str = Field(description="Search phrase to match against message contents.")
  chat_id: Optional[int] = Field(
    default=None,
    description="Restrict the search to one chat.",
  )
  limit: int = Field(
    default=10,
    ge=1,
    le=50,
    description="Max message excerpts to return.",
  )


class SearchChatHistoryTool(Tool):
  name = "search_chat_history"
  description = (
    "Search past chat messages for exact wording or excerpts. Prefer "
    "search_global_memory first; use this to recover raw details from "
    "previous conversations."
  )
  input_model = _SearchChatHistoryInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    return MemoryService.search_chat_history(
      arguments["query"],
      chat_id=arguments.get("chat_id"),
      limit=arguments["limit"],
    )


class _SaveMemoryInput(BaseModel):
  type: Literal["fact", "preference", "decision", "topic"] = Field(
    description="The kind of memory: fact, preference, decision, or topic."
  )
  content: str = Field(
    description="One durable piece of information to remember."
  )
  importance: int = Field(
    default=2,
    ge=1,
    le=4,
    description="1=low, 2=normal, 3=important, 4=critical.",
  )
  source_chat_id: Optional[int] = Field(
    default=None,
    description="The chat this memory was learned from.",
  )


class SaveMemoryTool(Tool):
  name = "save_memory"
  description = (
    "Save an important piece of information to global memory so it is reused "
    "across future conversations. Only save durable facts, user preferences, "
    "technical decisions, or established topics. Do NOT save greetings, "
    "temporary questions, or details relevant only to the current chat, and "
    "avoid saving content that already exists in memory."
  )
  input_model = _SaveMemoryInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    try:
      result = MemoryService.create(
        type_=arguments["type"],
        content=arguments["content"],
        importance=arguments["importance"],
        source_chat_id=arguments.get("source_chat_id"),
      )
      if result.get("duplicate"):
        return {"warning": "A similar memory already exists.", "memory": result["memory"]}
      return {"success": True, "memory": result}
    except ValueError as exc:
      return tool_error("invalid_input", str(exc))


class _ForgetMemoryInput(BaseModel):
  memory_id: Optional[int] = Field(
    default=None,
    description="Primary key of the memory to archive.",
  )
  query: Optional[str] = Field(
    default=None,
    description="Alternative to memory_id: forget entries matching this query.",
  )


class ForgetMemoryTool(Tool):
  name = "forget_memory"
  description = (
    "Archive a global memory entry so it no longer appears in active retrieval. "
    "Provide memory_id (from search_global_memory results) or a query to forget "
    "everything matching. Use when the user says a memory is wrong or outdated."
  )
  input_model = _ForgetMemoryInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    memory_id = arguments.get("memory_id")
    query = arguments.get("query")
    if memory_id is not None:
      if MemoryService.forget(memory_id):
        return {"success": True, "forgotten": [MemoryService.get(memory_id)]}
      return tool_error("not_found", f"Memory {memory_id} not found")
    if query:
      archived = MemoryService.forget_by_query(query)
      if not archived:
        return {"warning": f"No active memories matched {query!r}."}
      return {"success": True, "forgotten": archived}
    return tool_error("invalid_input", "Provide memory_id or query.")


def build_memory_tools() -> list[Tool]:
  """Instantiate the controlled memory tools for the agent's tool registry."""
  return [
    SearchGlobalMemoryTool(),
    GetMemoryTool(),
    GetChatContextTool(),
    SearchChatHistoryTool(),
    SaveMemoryTool(),
    ForgetMemoryTool(),
  ]