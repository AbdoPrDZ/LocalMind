"""Global memory service: persistence and retrieval of memory entries.

Independent of any business domain (Project/Task scaffolds). The LLM reaches
this layer only through the memory tools in ``tools/memory.py``.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import or_, select

from database import get_session
from models.chat import Chat
from models.memory import Memory
from models.message import Message

MEMORY_TYPES = ("fact", "preference", "decision", "topic")
MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 4


def normalize_content(content: str) -> str:
  return re.sub(r"\s+", " ", content.strip().lower())


def _json_safe(value: Any) -> Any:
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  return value


def _to_dict(memory: Memory) -> dict:
  return {
    col.name: _json_safe(getattr(memory, col.name))
    for col in memory.__table__.columns
  }


def _validate_type(type_: str) -> None:
  if type_ not in MEMORY_TYPES:
    raise ValueError(
      f"Unknown memory type '{type_}'. Valid types: {', '.join(MEMORY_TYPES)}."
    )


def _validate_importance(importance: int) -> None:
  if not MIN_IMPORTANCE <= importance <= MAX_IMPORTANCE:
    raise ValueError(
      f"Importance must be between {MIN_IMPORTANCE} and {MAX_IMPORTANCE}, got {importance}."
    )


class MemoryService:

  @staticmethod
  def create(
    type_: str,
    content: str,
    importance: int = 2,
    source_chat_id: Optional[int] = None,
  ) -> dict:
    _validate_type(type_)
    _validate_importance(importance)

    session = get_session()
    try:
      existing = None
      for memory in session.scalars(select(Memory)).all():
        if normalize_content(memory.content) == normalize_content(content):
          existing = memory
          break
      if existing is not None:
        return {"duplicate": True, "memory": _to_dict(existing)}

      memory = Memory(
        type=type_,
        content=content,
        importance=importance,
        source_chat_id=source_chat_id,
      )
      session.add(memory)
      session.commit()
      session.refresh(memory)
      return _to_dict(memory)
    except Exception:
      session.rollback()
      raise
    finally:
      session.close()

  @staticmethod
  def get(memory_id: int) -> Optional[dict]:
    session = get_session()
    try:
      memory = session.get(Memory, memory_id)
      return None if memory is None else _to_dict(memory)
    finally:
      session.close()

  @staticmethod
  def update(memory_id: int, **fields: Any) -> Optional[dict]:
    editable = {"type", "content", "importance", "source_chat_id"}
    updates = {key: value for key, value in fields.items() if key in editable}

    if "type" in updates:
      _validate_type(updates["type"])
    if "importance" in updates:
      _validate_importance(updates["importance"])

    session = get_session()
    try:
      memory = session.get(Memory, memory_id)
      if memory is None:
        return None

      for key, value in updates.items():
        setattr(memory, key, value)
      session.commit()
      session.refresh(memory)
      return _to_dict(memory)
    except Exception:
      session.rollback()
      raise
    finally:
      session.close()

  @staticmethod
  def delete(memory_id: int) -> bool:
    session = get_session()
    try:
      memory = session.get(Memory, memory_id)
      if memory is None:
        return False
      session.delete(memory)
      session.commit()
      return True
    except Exception:
      session.rollback()
      raise
    finally:
      session.close()

  @staticmethod
  def search(query: str, limit: int = 10) -> list[dict]:
    pattern = f"%{query.strip()}%"
    session = get_session()
    try:
      rows = session.scalars(
        select(Memory)
        .where(
          or_(
            Memory.content.ilike(pattern),
            Memory.type.ilike(pattern),
          )
        )
        .order_by(Memory.importance.desc(), Memory.updated_at.desc())
        .limit(limit)
      ).all()
      return [_to_dict(row) for row in rows]
    finally:
      session.close()

  @staticmethod
  def list(
    type_: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
  ) -> list[dict]:
    session = get_session()
    try:
      stmt = select(Memory).order_by(
        Memory.importance.desc(),
        Memory.updated_at.desc(),
      )
      if type_ is not None:
        stmt = stmt.where(Memory.type == type_)
      rows = session.scalars(stmt.offset(offset).limit(limit)).all()
      return [_to_dict(row) for row in rows]
    finally:
      session.close()

  @staticmethod
  def get_chat_context(chat_id: int) -> Optional[str]:
    session = get_session()
    try:
      chat = session.get(Chat, chat_id)
      return None if chat is None else chat.context
    finally:
      session.close()

  @staticmethod
  def search_chat_history(
    query: str,
    chat_id: Optional[int] = None,
    limit: int = 10,
  ) -> list[dict]:
    pattern = f"%{query.strip()}%"
    session = get_session()
    try:
      stmt = (
        select(Message)
        .where(Message.content.ilike(pattern))
        .order_by(Message.id.desc())
        .limit(limit)
      )
      if chat_id is not None:
        stmt = stmt.where(Message.chat_id == chat_id)
      rows = session.scalars(stmt).all()
      return [
        {
          "chat_id": row.chat_id,
          "message_id": row.id,
          "role": row.role,
          "content": row.content,
        }
        for row in rows
      ]
    finally:
      session.close()