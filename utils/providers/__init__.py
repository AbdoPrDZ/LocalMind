from utils.providers.base import LLMProvider
from utils.providers.free import FreeLLMProvider
from utils.providers.gemini import GeminiLLMProvider
from utils.providers.local import LocalLLMProvider
from utils.providers.openai import OpenAILLMProvider

PROVIDERS = {
  "local": LocalLLMProvider,
  "gemini": GeminiLLMProvider,
  "openai": OpenAILLMProvider,
  "free": FreeLLMProvider,
}

__all__ = [
  "LLMProvider",
  "LocalLLMProvider",
  "GeminiLLMProvider",
  "OpenAILLMProvider",
  "FreeLLMProvider",
  "PROVIDERS",
]