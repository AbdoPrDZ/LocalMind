import os

from sqlalchemy import create_engine, text
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
    note,
    project,
    settings,
    task,
    usage,
  )

  BaseModel.metadata.create_all(engine)

  # No Alembic/migration system: create_all() adds new *tables* but not new
  # columns on existing ones. Apply additive schema changes idempotently.
  _ensure_column("chats", "state", "TEXT")
  for column, column_type in (
    ("confidence", "REAL"),
    ("status", "VARCHAR(20)"),
    ("superseded_by", "INTEGER"),
    ("source_message_id", "INTEGER"),
    ("last_accessed_at", "DATETIME"),
    ("access_count", "INTEGER"),
  ):
    _ensure_column("memories", column, column_type)

  _setup_memory_fts()


_FTS_AVAILABLE = False


def is_fts_available() -> bool:
  """True when FTS5-backed memory search can be used."""
  return _FTS_AVAILABLE


def _setup_memory_fts() -> None:
  """Create the memories_fts5 full-text index and keep it in sync.

  FTS5 exists in stock CPython SQLite on Windows, but degrade gracefully to
  the ilike path if the build lacks it (setups with a custom/DLL sqlite).
  """
  global _FTS_AVAILABLE

  try:
    with engine.begin() as conn:
      conn.execute(
        text(
          "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
          "content, content='memories', content_rowid='id', tokenize='unicode61')"
        )
      )
    with engine.begin() as conn:
      for trigger, sql in (
        (
          "memories_fts_ai",
          "CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories "
          "BEGIN INSERT INTO memories_fts(rowid, content) "
          "VALUES (new.id, new.content); END",
        ),
        (
          "memories_fts_ad",
          "CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories "
          "BEGIN INSERT INTO memories_fts(memories_fts, rowid, content) "
          "VALUES ('delete', old.id, old.content); END",
        ),
        (
          "memories_fts_au",
          "CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories "
          "BEGIN INSERT INTO memories_fts(memories_fts, rowid, content) "
          "VALUES ('delete', old.id, old.content); "
          "INSERT INTO memories_fts(rowid, content) "
          "VALUES (new.id, new.content); END",
        ),
      ):
        conn.execute(text(sql))
    with engine.begin() as conn:
      conn.execute(
        text(
          "INSERT INTO memories_fts(memories_fts, rowid, content) "
          "SELECT 'delete', id, content FROM memories"
        )
      )
      conn.execute(
        text("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")
      )
    _FTS_AVAILABLE = True
  except Exception:
    _FTS_AVAILABLE = False


def _ensure_column(table: str, column: str, column_type: str) -> None:
  """Add a column to an existing table if it is missing (idempotent)."""
  with engine.begin() as conn:
    existing = {
      row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))
    }
  if column not in existing:
    with engine.begin() as conn:
      conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))
