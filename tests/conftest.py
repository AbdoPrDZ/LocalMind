import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(tempfile.gettempdir(), 'localmind_test.db')}"
os.environ["LLM_LOCAL_MODELS_DIR"] = os.path.join(os.getcwd(), "resources", "models")
os.environ["LLM_LOCAL_MODEL_NAME"] = "qwen3-4b-instruct-gguf"

import pytest  # noqa: E402

from utils.model import BaseModel  # noqa: E402


@pytest.fixture()
def db():
  from database import engine, init_db

  BaseModel.metadata.drop_all(engine)
  init_db()
  yield


@pytest.fixture(autouse=True)
def _stub_title_gen(monkeypatch):
  # Title generation would otherwise build the LLM provider (heavy); tests
  # that exercise it re-patch _generated_title explicitly.
  import apps.base

  monkeypatch.setattr(apps.base, "_generated_title", lambda msg: (None, None))