from utils.providers.base import LLMProvider
from utils.providers.gemini import GeminiLLMProvider
from utils.providers.local import LocalLLMProvider

PROVIDERS = {
  "local": LocalLLMProvider,
  "gemini": GeminiLLMProvider,
}

__all__ = [
  "LLMProvider",
  "LocalLLMProvider",
  "GeminiLLMProvider",
  "PROVIDERS",
]