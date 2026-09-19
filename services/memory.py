"""Global memory service: persistence and retrieval of memory entries.

Independent of any business domain (Project/Task scaffolds). The LLM reaches
this layer only through the memory tools in ``tools/memory.py``.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import or_, select, text

from database import get_session
from models.chat import Chat
from models.memory import Memory
from models.message import Message
from utils.time import utcnow

MEMORY_TYPES = ("fact", "preference", "decision", "topic")
MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 4

ACTIVE_STATUS = "active"
SUPERSEDED_STATUS = "superseded"
ARCHIVED_STATUS = "archived"

_SUBJECT_OVERLAP_THRESHOLD = 0.5


def normalize_content(content: str) -> str:
  return re.sub(r"\s+", " ", content.strip().lower())


def _content_tokens(content: str) -> set[str]:
  return set(re.findall(r"[a-z0-9]+", content.lower()))


#: Generic filler words that carry no subject information for contradiction
#: detection ("User prefers X." vs "User prefers Y." share only boilerplate).
_BOILERPLATE = frozenset({
  "a", "an", "the", "and", "or", "but", "for", "nor", "on", "at", "to",
  "from", "by", "of", "in", "is", "are", "was", "were", "be", "been",
  "am", "do", "does", "did", "will", "would", "can", "could", "should",
  "may", "might", "must", "user", "users", "prefer", "prefers", "use", "uses",
  "with", "without", "as", "than", "so", "if", "then", "now", "to", "very",
  "just", "you", "your", "it", "its", "this", "that", "these", "those",
})


def _subject_tokens(content: str) -> set[str]:
  return _content_tokens(content) - _BOILERPLATE


def _subject_overlap(left: str, right: str) -> float:
  """Shared subject-token ratio (of the shorter text); contradiction heuristic.

  Boilerplate words are ignored so that "User prefers X." and "User prefers Y."
  do not look like the same subject, while "LocalMind uses SQLite." and
  "LocalMind uses PostgreSQL." still do (shared subject "localmind").
  """
  left_tokens = _subject_tokens(left)
  right_tokens = _subject_tokens(right)
  if not left_tokens or not right_tokens:
    return 0.0
  return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


def _json_safe(value: Any) -> Any:
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  return value


def _to_dict(memory: Memory) -> dict:
  data = {
    col.name: _json_safe(getattr(memory, col.name))
    for col in memory.__table__.columns
  }
  # Normalize row defaults for columns added to pre-existing databases.
  data["status"] = data.get("status") or ACTIVE_STATUS
  data["confidence"] = data["confidence"] if data.get("confidence") is not None else 0.5
  data["access_count"] = data.get("access_count") or 0
  return data


def _active(memory_status: str | None) -> bool:
  return memory_status is None or memory_status == ACTIVE_STATUS


def _active_filter():
  return or_(Memory.status.is_(None), Memory.status == ACTIVE_STATUS)


def _fts_query(query: str) -> str:
  """Safe FTS5 MATCH query: alphanumeric tokens quoted, implicit AND."""
  tokens = re.findall(r"[a-z0-9]{2,}", query.lower())
  return " ".join(f'"{token}"' for token in tokens)


def _fts_candidate_rows(session, query: str):
  """Rowids from the FTS5 index ordered by bm25 (best first)."""
  from database import is_fts_available

  if not is_fts_available():
    return None
  fts_query = _fts_query(query)
  try:
    rows = session.execute(
      text(
        "SELECT rowid FROM memories_fts "
        "WHERE memories_fts MATCH :query ORDER BY bm25(memories_fts) LIMIT 200"
      ),
      {"query": fts_query},
    ).all()
  except Exception:
    return None
  if not rows:
    return None
  return session.scalars(
    select(Memory).where(Memory.id.in_([row[0] for row in rows])).order_by(Memory.updated_at.desc())
  ).all()


def _supersede_conflicts(session, memory: Memory) -> None:
  """Mark same-subject active memories of the same type as superseded."""
  peers = session.scalars(
    select(Memory).where(
      or_(Memory.status.is_(None), Memory.status == ACTIVE_STATUS),
      Memory.type == memory.type,
    )
  ).all()
  for row in peers:
    if row.id == memory.id:
      continue
    if normalize_content(row.content) == normalize_content(memory.content):
      continue
    if _subject_overlap(row.content, memory.content) >= _SUBJECT_OVERLAP_THRESHOLD:
      row.status = SUPERSEDED_STATUS
      row.superseded_by = memory.id


def _search_score(query: str, row: Memory) -> float:
  """Rank search candidates: keyword relevance × importance × confidence."""
  # Local import: ranking lives under services/context, which imports this
  # module via retrieval (loading-time cycle would otherwise deadlock it).
  from services.context.ranking import keyword_overlap

  keyword = keyword_overlap(query, row.content)
  importance = max(row.importance, 1) / 4.0
  confidence = row.confidence if row.confidence is not None else 0.5
  return 0.7 * keyword + 0.2 * importance + 0.1 * confidence


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
    source_message_id: Optional[int] = None,
    confidence: float = 0.5,
  ) -> dict:
    _validate_type(type_)
    _validate_importance(importance)
    if not 0.0 <= confidence <= 1.0:
      raise ValueError("Confidence must be between 0 and 1.")

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
        confidence=confidence,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
      )
      session.add(memory)
      session.flush()
      _supersede_conflicts(session, memory)
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
    editable = {
      "type",
      "content",
      "importance",
      "confidence",
      "status",
      "source_chat_id",
    }
    updates = {key: value for key, value in fields.items() if key in editable}

    if "type" in updates:
      _validate_type(updates["type"])
    if "importance" in updates:
      _validate_importance(updates["importance"])
    if "confidence" in updates and not 0.0 <= updates["confidence"] <= 1.0:
      raise ValueError("Confidence must be between 0 and 1.")
    if "status" in updates and updates["status"] not in (
      ACTIVE_STATUS,
      SUPERSEDED_STATUS,
      ARCHIVED_STATUS,
    ):
      raise ValueError(f"Unknown memory status: {updates['status']}")

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
  def forget(memory_id: int) -> bool:
    """Archive a memory so it stops appearing in active retrieval."""
    session = get_session()
    try:
      memory = session.get(Memory, memory_id)
      if memory is None:
        return False
      memory.status = ARCHIVED_STATUS
      session.commit()
      return True
    except Exception:
      session.rollback()
      raise
    finally:
      session.close()

  @staticmethod
  def forget_by_query(query: str, limit: int = 5) -> list[dict]:
    """Archive memories matching a query; returns the archived entries."""
    pattern = f"%{query.strip()}%"
    session = get_session()
    try:
      rows = session.scalars(
        select(Memory)
        .where(
          or_(Memory.status.is_(None), Memory.status == ACTIVE_STATUS),
          or_(Memory.content.ilike(pattern), Memory.type.ilike(pattern)),
        )
        .order_by(Memory.importance.desc(), Memory.updated_at.desc())
        .limit(limit)
      ).all()
      archived = []
      for row in rows:
        row.status = ARCHIVED_STATUS
        archived.append(_to_dict(row))
      session.commit()
      return archived
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
      rows = None
      if _fts_query(query):
        rows = _fts_candidate_rows(session, query)
      if rows is None:
        rows = session.scalars(
          select(Memory)
          .where(
            _active_filter(),
            or_(
              Memory.content.ilike(pattern),
              Memory.type.ilike(pattern),
            ),
          )
          .order_by(Memory.importance.desc(), Memory.updated_at.desc())
          .limit(200)
        ).all()
      rows = [row for row in rows if _active(row.status)]
      now = utcnow()
      for row in rows:
        row.access_count += 1
        row.last_accessed_at = now
      session.commit()
      scored = sorted(
        rows,
        key=lambda row: _search_score(query, row),
        reverse=True,
      )
      return [_to_dict(row) for row in scored[:limit]]
    finally:
      session.close()

  @staticmethod
  def list(
    type_: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    status: Optional[str] = None,
  ) -> list[dict]:
    session = get_session()
    try:
      stmt = select(Memory).order_by(
        Memory.status.desc(),
        Memory.importance.desc(),
        Memory.updated_at.desc(),
      )
      if status is None:
        stmt = stmt.where(
          or_(Memory.status.is_(None), Memory.status == ACTIVE_STATUS),
        )
      else:
        stmt = stmt.where(Memory.status == status)
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
  def conflicts() -> list[dict]:
    """Active same-type memory pairs sharing a subject (potential conflicts).

    Pairs whose contents are contradictory but were never auto-superseded —
    surfaced for review via ``/global conflicts``.
    """
    session = get_session()
    try:
      rows = session.scalars(select(Memory).where(_active_filter())).all()
    finally:
      session.close()

    conflict_pairs: list[dict] = []
    for i in range(len(rows)):
      for j in range(i + 1, len(rows)):
        left, right = rows[i], rows[j]
        if left.type != right.type:
          continue
        if normalize_content(left.content) == normalize_content(right.content):
          continue
        overlap = _subject_overlap(left.content, right.content)
        if overlap < _SUBJECT_OVERLAP_THRESHOLD:
          continue
        conflict_pairs.append({
          "left": _to_dict(left),
          "right": _to_dict(right),
          "shared_subject": sorted(_subject_tokens(left.content) & _subject_tokens(right.content)),
          "overlap": round(overlap, 3),
        })
    return conflict_pairs

  @staticmethod
  def stale(days: int = 30) -> list[dict]:
    """Active memories never re-accessed and older than ``days``."""
    cutoff = utcnow() - timedelta(days=days)
    session = get_session()
    try:
      rows = session.scalars(
        select(Memory)
        .where(
          _active_filter(),
          Memory.access_count == 0,
          Memory.updated_at < cutoff,
        )
        .order_by(Memory.updated_at.asc())
      ).all()
      return [_to_dict(row) for row in rows]
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