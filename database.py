import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from utils.env import ENV
from utils.model import BaseModel


def _prepare_database_url(url: str) -> str:
  # SQLite requires its parent directory to already exist.
  if url.startswith("sqlite:///"):
    directory = os.path.dirname(url.replace("sqlite:///", ""))
    if directory:
      os.makedirs(directory, exist_ok=True)
  return url


engine = create_engine(
  _prepare_database_url(ENV.get_database_url()),
  echo=False,
)

SessionLocal = sessionmaker(
  bind=engine,
  autoflush=False,
  autocommit=False,
)


def get_session():
  return SessionLocal()


def init_db() -> None:
  from models import (  # noqa: F401  (register tables)
    chat,
    memory,
    message,
    project,
    settings,
    task,
    usage,
  )

  BaseModel.metadata.create_all(engine)
