from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from utils.registry import register_model
from utils.time import utcnow

from utils.model import BaseModel


@register_model(delete=False)
class Project(BaseModel):
  __tablename__ = "projects"
  __description__ = "A project that groups tasks."

  id: Mapped[int] = mapped_column(primary_key=True)
  name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
  description: Mapped[str | None] = mapped_column(Text, nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

  tasks: Mapped[list["Task"]] = relationship(
    back_populates="project",
    cascade="all, delete-orphan",
  )