import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(tempfile.gettempdir(), 'localmind_test.db')}"
os.environ["MODELS_DIR"] = os.path.join(os.getcwd(), "resources", "models")
os.environ["MODEL_NAME"] = "qwen3-4b-instruct-gguf"

import pytest  # noqa: E402

from utils.model import BaseModel  # noqa: E402


@pytest.fixture()
def db():
  from database import engine, init_db

  BaseModel.metadata.drop_all(engine)
  init_db()
  yield