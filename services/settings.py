"""Runtime LLM selection persisted in the ``settings`` table.

``.env`` provides the defaults (``LLM_PROVIDER``, ``GEMINI_MODEL``,
``MODEL_NAME``); the ``settings`` table stores the runtime override the user
picks with the cmd app's ``/select model`` command. Overrides are applied to
the environment before a provider is built (``apply_to_env``) and read for
usage accounting via ``resolve_provider()`` / ``resolve_model()``.
"""

import os

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from database import get_session
from models.settings import Setting
from utils.env import DEFAULT_OPENAI_MODEL, ENV
from utils.providers.gemini import DEFAULT_GEMINI_MODEL

PROVIDER_KEY = "provider"


def _provider_model_key(provider: str) -> str:
  return f"{provider}_model"


def _model_env(provider: str) -> str:
  """Env var that feeds the model default for a provider."""
  if provider == "gemini":
    return "GEMINI_MODEL"
  if provider == "openai":
    return "OPENAI_MODEL"
  if provider == "free":
    return "FREE_MODEL"
  return "MODEL_NAME"


class SettingsService:

  @staticmethod
  def get(key: str, default: str | None = None) -> str | None:
    """Read a setting; returns ``default`` when the table is unavailable."""
    try:
      session = get_session()
      try:
        row = session.execute(
          select(Setting).where(Setting.key == key)
        ).scalar_one_or_none()
        return row.value if row is not None else default
      finally:
        session.close()
    except OperationalError:
      return default

  @staticmethod
  def set(key: str, value: str) -> None:
    session = get_session()
    try:
      row = session.execute(
        select(Setting).where(Setting.key == key)
      ).scalar_one_or_none()
      if row is None:
        session.add(Setting(key=key, value=value))
      else:
        row.value = value
      session.commit()
    finally:
      session.close()

  @staticmethod
  def get_provider() -> str | None:
    return SettingsService.get(PROVIDER_KEY)

  @staticmethod
  def set_provider(provider: str) -> None:
    SettingsService.set(PROVIDER_KEY, provider)

  @staticmethod
  def get_model(provider: str) -> str | None:
    return SettingsService.get(_provider_model_key(provider))

  @staticmethod
  def set_model(provider: str, model: str) -> None:
    SettingsService.set(_provider_model_key(provider), model)

  @staticmethod
  def apply_to_env() -> None:
    """Push persisted selections into ``os.environ`` so provider builds use them.

    Only overrides matching rows that actually exist; otherwise the ``.env``
    values keep working as defaults.
    """
    provider = SettingsService.get_provider()
    if provider is None:
      return
    os.environ["LLM_PROVIDER"] = provider
    model = SettingsService.get_model(provider)
    if model is not None:
      os.environ[_model_env(provider)] = model


def resolve_provider() -> str:
  """Active provider: persisted setting, else the ``LLM_PROVIDER`` default."""
  stored = SettingsService.get_provider()
  if stored:
    return stored
  return (ENV.get("LLM_PROVIDER", default="local") or "local").strip().lower()


def resolve_model(provider: str) -> str:
  """Active model for a provider: persisted setting, else the default."""
  stored = SettingsService.get_model(provider)
  if stored:
    return stored
  if provider == "gemini":
    return ENV.get("GEMINI_MODEL", default=DEFAULT_GEMINI_MODEL)
  if provider == "openai":
    return ENV.get("OPENAI_MODEL", default=DEFAULT_OPENAI_MODEL)
  if provider == "free":
    from utils.providers.free import DEFAULT_FREE_MODEL

    return ENV.get("FREE_MODEL", default=DEFAULT_FREE_MODEL)
  return ENV.get("MODEL_NAME", default="unknown")