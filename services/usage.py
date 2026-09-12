"""Usage accounting: estimated cost per chat session and global aggregations.

One ``Usage`` row is opened when a chat starts/resumes, accumulates tokens and
estimated cost per LLM call, and is closed when the chat is exited.

Costs are estimates. Gemini pricing (per 1M tokens, USD paid tier) is looked
up from ``GEMINI_PRICING_PER_1M`` — including Gemini models used through the
``openai`` provider; the Gemini API does not expose the exact billed amount or
a key's remaining quota. Free routers and local llama-cpp runs are free
(cost 0) but tokens are still counted.
"""

from sqlalchemy import func, select

from database import get_session
from models.usage import Usage
from utils.time import utcnow

#: Gemini results exclude nothing: ``-preview``/``-latest`` suffixes are
#: stripped before lookup.
#: (input USD per 1M tokens, output USD per 1M tokens)
GEMINI_PRICING_PER_1M = {
  "gemini-2.0-flash": (1.00, 3.00),
  "gemini-2.5-flash": (0.30, 2.50),
  "gemini-3-flash": (1.50, 9.00),
  "gemini-3.5-flash": (1.50, 9.00),
  "gemini-3.5-flash-lite": (0.75, 4.50),
}

#: Fallback for models without an entry in the pricing table.
GEMINI_DEFAULT_PRICE = (2.50, 15.00)

_FREE_PROVIDER_COST = 0.0


def model_base(model: str) -> str:
  """Lowercase model id with ``-preview``/``-latest`` suffixes removed."""
  name = (model or "").strip().lower()
  for suffix in ("-preview", "-latest"):
    if name.endswith(suffix):
      name = name[: -len(suffix)]
  return name


def estimate_cost(
  provider: str,
  model: str,
  prompt_tokens: int,
  completion_tokens: int,
) -> float:
  """Estimated USD cost of a call.

  Gemini models are billed per token no matter which provider serves them
  (``gemini`` or ``openai``); everything else is free.
  """
  base = model_base(model)
  if provider != "gemini" and not base.startswith("gemini"):
    return _FREE_PROVIDER_COST

  input_price, output_price = GEMINI_PRICING_PER_1M.get(base, GEMINI_DEFAULT_PRICE)
  return (
    prompt_tokens * input_price + completion_tokens * output_price
  ) / 1_000_000


def _session_dict(row: Usage) -> dict:
  return {
    "id": row.id,
    "chat_id": row.chat_id,
    "provider": row.provider,
    "model": row.model,
    "started_at": row.started_at,
    "ended_at": row.ended_at,
    "prompt_tokens": row.prompt_tokens,
    "completion_tokens": row.completion_tokens,
    "total_tokens": row.total_tokens,
    "cost": row.cost,
  }


class UsageService:

  @staticmethod
  def start_session(chat_id: int, provider: str, model: str) -> int:
    """Open a usage session for a chat (called on chat start/resume)."""
    session = get_session()
    try:
      row = Usage(chat_id=chat_id, provider=provider, model=model)
      session.add(row)
      session.commit()
      session.refresh(row)
      session.expunge(row)
      return row.id
    finally:
      session.close()

  @staticmethod
  def record(session_id: int, prompt_tokens: int, completion_tokens: int) -> None:
    """Accumulate tokens and estimated cost into an open session."""
    session = get_session()
    try:
      row = session.get(Usage, session_id)
      if row is None:
        return

      row.prompt_tokens += max(prompt_tokens, 0)
      row.completion_tokens += max(completion_tokens, 0)
      row.total_tokens += max(prompt_tokens, 0) + max(completion_tokens, 0)
      row.cost += estimate_cost(row.provider, row.model, prompt_tokens, completion_tokens)
      session.commit()
      session.refresh(row)
      session.expunge(row)
    finally:
      session.close()

  @staticmethod
  def close_session(session_id: int) -> None:
    """Close a usage session (idempotent) once its chat is exited."""
    session = get_session()
    try:
      row = session.get(Usage, session_id)
      if row is not None and row.ended_at is None:
        row.ended_at = utcnow()
        session.commit()
        session.refresh(row)
        session.expunge(row)
    finally:
      session.close()

  @staticmethod
  def get_session(session_id: int) -> dict | None:
    session = get_session()
    try:
      row = session.get(Usage, session_id)
      if row is None:
        return None
      session.expunge(row)
      return _session_dict(row)
    finally:
      session.close()

  @staticmethod
  def totals() -> dict:
    """Global usage across every recorded session."""
    session = get_session()
    try:
      row = session.execute(
        select(
          func.count(Usage.id),
          func.coalesce(func.sum(Usage.prompt_tokens), 0),
          func.coalesce(func.sum(Usage.completion_tokens), 0),
          func.coalesce(func.sum(Usage.total_tokens), 0),
          func.coalesce(func.sum(Usage.cost), 0.0),
        )
      ).one()
      return {
        "sessions": row[0],
        "prompt_tokens": row[1],
        "completion_tokens": row[2],
        "total_tokens": row[3],
        "cost": row[4],
      }
    finally:
      session.close()

  @staticmethod
  def totals_for_chat(chat_id: int) -> dict:
    """Global usage aggregated over all sessions of one chat."""
    session = get_session()
    try:
      row = session.execute(
        select(
          func.count(Usage.id),
          func.coalesce(func.sum(Usage.prompt_tokens), 0),
          func.coalesce(func.sum(Usage.completion_tokens), 0),
          func.coalesce(func.sum(Usage.total_tokens), 0),
          func.coalesce(func.sum(Usage.cost), 0.0),
        ).where(Usage.chat_id == chat_id)
      ).one()
      return {
        "sessions": row[0],
        "prompt_tokens": row[1],
        "completion_tokens": row[2],
        "total_tokens": row[3],
        "cost": row[4],
      }
    finally:
      session.close()

  @staticmethod
  def totals_by_chat() -> list[dict]:
    """Per-chat totals, most expensive first."""
    session = get_session()
    try:
      rows = session.execute(
        select(
          Usage.chat_id,
          func.count(Usage.id),
          func.coalesce(func.sum(Usage.prompt_tokens), 0),
          func.coalesce(func.sum(Usage.completion_tokens), 0),
          func.coalesce(func.sum(Usage.total_tokens), 0),
          func.coalesce(func.sum(Usage.cost), 0.0),
        )
        .group_by(Usage.chat_id)
        .order_by(func.sum(Usage.cost).desc())
      ).all()
      return [
        {
          "chat_id": row[0],
          "sessions": row[1],
          "prompt_tokens": row[2],
          "completion_tokens": row[3],
          "total_tokens": row[4],
          "cost": row[5],
        }
        for row in rows
      ]
    finally:
      session.close()

  @staticmethod
  def totals_by_model() -> list[dict]:
    """Per-model totals, most expensive first."""
    session = get_session()
    try:
      rows = session.execute(
        select(
          Usage.provider,
          Usage.model,
          func.count(Usage.id),
          func.coalesce(func.sum(Usage.prompt_tokens), 0),
          func.coalesce(func.sum(Usage.completion_tokens), 0),
          func.coalesce(func.sum(Usage.total_tokens), 0),
          func.coalesce(func.sum(Usage.cost), 0.0),
        )
        .group_by(Usage.provider, Usage.model)
        .order_by(func.sum(Usage.cost).desc())
      ).all()
      return [
        {
          "provider": row[0],
          "model": row[1],
          "sessions": row[2],
          "prompt_tokens": row[3],
          "completion_tokens": row[4],
          "total_tokens": row[5],
          "cost": row[6],
        }
        for row in rows
      ]
    finally:
      session.close()