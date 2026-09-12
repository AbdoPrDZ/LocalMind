from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from utils.model import BaseModel
from utils.registry import register_model
from utils.time import utcnow


@register_model()
class Note(BaseModel):
  __tablename__ = "notes"
  __description__ = "A quick note with a title, text, and optional tags."

  id: Mapped[int] = mapped_column(primary_key=True)
  title: Mapped[str] = mapped_column(String(200), index=True)
  content: Mapped[str] = mapped_column(Text)
  tags: Mapped[str | None] = mapped_column(String(300), nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
  updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)