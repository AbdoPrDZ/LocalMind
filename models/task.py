from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from utils.registry import register_model
from utils.model import BaseModel


@register_model()
class Task(BaseModel):
  __tablename__ = "tasks"
  __description__ = "A to-do item inside a project."

  id: Mapped[int] = mapped_column(primary_key=True)
  project_id: Mapped[int] = mapped_column(
    ForeignKey("projects.id"),
    index=True,
  )
  title: Mapped[str] = mapped_column(String(200))
  status: Mapped[str] = mapped_column(String(30), default="todo")

  project: Mapped["Project"] = relationship(back_populates="tasks")