import os
from pathlib import Path

import dotenv

REQUIRED_ENV_VARS = [
  "DATABASE_URL",
  "MODELS_DIR",
  "MODEL_NAME",
]

# .env sits next to modren-app/ (independent of the current working dir).
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

class ENV:

  @staticmethod
  def init():
    dotenv.load_dotenv(ENV_PATH, override=False)

    for var in REQUIRED_ENV_VARS:
      if os.getenv(var) is None:
        raise ValueError(f"Environment variable {var} is required but not set.")

  @staticmethod
  def get(key: str, default: str = None, required: bool = False) -> str:
    value = os.getenv(key, default)

    if required and value is None:
      raise ValueError(f"Environment variable {key} is required but not set.")

    return value

  @staticmethod
  def get_database_url() -> str:
    return ENV.get("DATABASE_URL", required=True)

  @staticmethod
  def get_models_dir() -> str:
    path = ENV.get("MODELS_DIR", required=True)

    if not os.path.exists(path):
      raise ValueError(f"Models directory {path} does not exist.")

    return path

  @staticmethod
  def get_model_path() -> str:
    name =  ENV.get("MODEL_NAME", required=True)
    path = os.path.join(ENV.get_models_dir(), name, "model.gguf")

    if not os.path.exists(path):
      raise ValueError(f"Model {name} does not exist in models directory.")

    return path

  @staticmethod
  def get_system_prompt(default: str) -> str:
    path = ENV.get("SYSTEM_PROMPT_PATH", default=None)

    if path:
      if not os.path.exists(path):
        raise ValueError(f"System prompt file {path} does not exist.")
      with open(path, "r", encoding="utf-8") as f:
        return f.read()

    return default
