from typing import Optional

from llama_cpp import Llama

from utils.env import ENV

_llm: Optional[Llama] = None


def get_llm() -> Llama:
  """Lazily load the local model once and reuse it (singleton)."""
  global _llm

  if _llm is None:
    _llm = Llama(
      model_path=ENV.get_model_path(),

      # Context window
      n_ctx=int(ENV.get("MODEL_CONTEXT_WINDOW", default=4096)),

      # CPU threads
      n_threads=int(ENV.get("MODEL_CPU_THREADS", default=8)),

      # Number of GPU layers
      # 0 = CPU only
      n_gpu_layers=int(ENV.get("MODEL_GPU_LAYERS", default=0)),

      verbose=ENV.get("MODEL_VERBOSE", default="false").lower()
      in {"1", "true", "yes"},
    )

  return _llm
