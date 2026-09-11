from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from utils.registry import register_model
from utils.time import utcnow

from utils.model import BaseModel


@register_model(create=False, update=False, delete=False)
class Message(BaseModel):
  __tablename__ = "messages"
  __description__ = "A single user or assistant message inside a chat."

  id: Mapped[int] = mapped_column(primary_key=True)
  chat_id: Mapped[int] = mapped_column(
    ForeignKey("chats.id"),
    index=True,
  )
  role: Mapped[str] = mapped_column(String(20))
  content: Mapped[str] = mapped_column(Text)
  created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

  chat: Mapped["Chat"] = relationship(back_populates="messages")