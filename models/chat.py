from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from utils.registry import register_model
from utils.time import utcnow

from utils.model import BaseModel


@register_model(create=False, update=False, delete=False)
class Chat(BaseModel):
  __tablename__ = "chats"
  __description__ = "A conversation with the local assistant."

  id: Mapped[int] = mapped_column(primary_key=True)
  title: Mapped[str | None] = mapped_column(String(200), nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

  context: Mapped[str | None] = mapped_column(Text, nullable=True)

  messages: Mapped[list["Message"]] = relationship(
    back_populates="chat",
    cascade="all, delete-orphan",
  )