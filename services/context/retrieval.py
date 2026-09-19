"""Query-aware retrieval: pick relevant global memories and old chat messages.

Phase 1 uses the keyword+importance+recency ranker from ``ranking.py`` (no
embedding model yet; FTS5 keyword search lands in Phase 3). The engine asks
for the few most relevant entries, not a fixed global snapshot.
"""

from sqlalchemy import select

from database import get_session
from models.chat import Chat as ChatRecord
from models.message import Message
from services.context.ranking import keyword_overlap, score_memory, score_message
from services.memory import MemoryService

_RELEVANT_IMPORTANCE = 3


def _memory_eligible(keyword: float, importance: int) -> bool:
  """Inject keyword matches plus high-importance memories; skip the rest."""
  return keyword > 0.0 or importance >= _RELEVANT_IMPORTANCE


def retrieve_memories(query: str, limit: int = 5) -> list[dict]:
  """Rank global memories by relevance to ``query`` and return the top ones."""
  candidates = MemoryService.list(limit=500)
  scored = []
  for memory in candidates:
    keyword = keyword_overlap(query, memory["content"])
    if not _memory_eligible(keyword, memory["importance"]):
      continue
    score = score_memory(
      query,
      memory["content"],
      memory["importance"],
      memory.get("updated_at"),
    )
    scored.append((score, memory))

  scored.sort(key=lambda pair: pair[0], reverse=True)
  return [dict(memory, score=round(score, 3)) for score, memory in scored[:limit]]


def retrieve_history(
  query: str,
  chat_id: int,
  limit: int = 3,
  exclude_message_ids: set[int] | None = None,
) -> list[dict]:
  """Rank older messages in ``chat_id`` by relevance; current turn is excluded."""
  exclude = set(exclude_message_ids or ())
  session = get_session()
  try:
    rows = session.scalars(
      select(Message)
      .where(Message.chat_id == chat_id)
      .order_by(Message.id.desc())
    ).all()
  finally:
    session.close()

  scored = []
  for row in rows:
    if row.id in exclude:
      continue
    score = score_message(query, row.content, row.role)
    if score <= 0.0:
      continue
    scored.append((score, {
      "message_id": row.id,
      "role": row.role,
      "content": row.content,
      "score": round(score, 3),
    }))

  scored.sort(key=lambda pair: pair[0], reverse=True)
  return [item for _, item in scored[:limit]]