from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from utils.model import BaseModel
from utils.time import utcnow


class Memory(BaseModel):
  """A persistent global memory entry (fact, preference, decision, topic).

  Deliberately NOT registered via ``@register_model``: the LLM reaches memories
  only through the controlled memory tools → ``MemoryService``.
  """

  __tablename__ = "memories"
  __description__ = "A persistent global memory entry."

  id: Mapped[int] = mapped_column(primary_key=True)
  type: Mapped[str] = mapped_column(String(20), index=True)
  content: Mapped[str] = mapped_column(Text)
  importance: Mapped[int] = mapped_column(SmallInteger, default=2)
  source_chat_id: Mapped[int | None] = mapped_column(
    ForeignKey("chats.id"),
    nullable=True,
    index=True,
  )
  created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
  updated_at: Mapped[datetime] = mapped_column(
    DateTime,
    default=utcnow,
    onupdate=utcnow,
  )