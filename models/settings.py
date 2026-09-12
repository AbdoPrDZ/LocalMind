from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from utils.model import BaseModel


class Setting(BaseModel):
  """A single persisted runtime setting (key → value).

  Stores the user's runtime-selected LLM provider and model, overriding the
  `.env` defaults (which still act as fallbacks). Private store: never
  registered via ``@register_model``, so the LLM can neither see nor touch it
  (same policy as ``Memory`` and ``Usage``).
  """

  __tablename__ = "settings"

  id: Mapped[int] = mapped_column(primary_key=True)
  key: Mapped[str] = mapped_column(String(50), unique=True)
  value: Mapped[str] = mapped_column(String(200))