from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from utils.model import BaseModel
from utils.time import utcnow


class Usage(BaseModel):
  """Token/cost usage for one chat session.

  A row is opened every time a chat is started or resumed (``started_at``) and
  closed when the chat is exited (``ended_at``). Token counts and the estimated
  cost accumulate per LLM call while the session is open, and always reference
  the chat that spent them plus the provider/model used.

  Deliberately NOT registered via ``@register_model``: it is a private
  accounting store the LLM never sees or modifies.
  """

  __tablename__ = "usage"
  __description__ = "Token and estimated-cost usage for a chat session."

  id: Mapped[int] = mapped_column(primary_key=True)
  chat_id: Mapped[int] = mapped_column(
    ForeignKey("chats.id"),
    index=True,
  )
  provider: Mapped[str] = mapped_column(String(20))
  model: Mapped[str] = mapped_column(String(100))
  started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
  ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
  prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
  completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
  total_tokens: Mapped[int] = mapped_column(Integer, default=0)
  cost: Mapped[float] = mapped_column(Float, default=0.0)