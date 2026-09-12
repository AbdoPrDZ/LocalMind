from typing import Optional

from services.settings import SettingsService
from utils.env import ENV
from utils.providers import PROVIDERS
from utils.providers.base import LLMProvider

_llm: Optional[LLMProvider] = None


def get_llm() -> LLMProvider:
  """Lazily build the configured LLM provider once and reuse it (singleton).

  Persisted provider/model overrides are applied to the environment before
  construction so the provider reflects the current ``/select model`` choice.
  Provider is selected by ``LLM_PROVIDER``: ``local`` (default) or ``gemini``.
  """
  global _llm

  if _llm is None:
    SettingsService.apply_to_env()

    name = ENV.get_llm_provider()

    provider_cls = PROVIDERS.get(name)
    if provider_cls is None:
      raise ValueError(
        f"Unknown LLM provider '{name}'. "
        f"Available providers: {', '.join(PROVIDERS)}."
      )

    _llm = provider_cls()

  return _llm


def reset_llm() -> None:
  """Drop the cached provider so the next ``get_llm()`` rebuilds it.

  Called when the provider or model changes at runtime via ``/select model``.
  """
  global _llm
  _llm = None