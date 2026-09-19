import os

import pytest

from database import get_session
from models.chat import Chat as ChatRecord
from services.settings import (
  SettingsService,
  resolve_model,
  resolve_provider,
)
from utils.llm import get_llm, reset_llm


# ---------------------------------------------------------------------------
# SettingsService storage (settings table)
# ---------------------------------------------------------------------------


def test_settings_empty_by_default(db):
  assert SettingsService.get("provider") is None
  assert SettingsService.get("nope") is None
  assert SettingsService.get("nope", default="dflt") == "dflt"


def test_settings_get_after_missing_table():
  # The table may not exist yet in some flows; a read must not blow up.
  assert SettingsService.get("provider", default="local") == "local"


def test_settings_set_get_and_upsert(db):
  SettingsService.set_provider("gemini")
  assert SettingsService.get_provider() == "gemini"
  assert SettingsService.get_model("gemini") is None

  SettingsService.set_model("gemini", "gemini-3.5-flash")
  assert SettingsService.get_model("gemini") == "gemini-3.5-flash"

  # Same key updates in place (no duplicate rows).
  SettingsService.set_model("gemini", "gemini-3.5-flash-lite")
  session = get_session()
  try:
    from models.settings import Setting

    rows = session.query(Setting).filter(Setting.key == "gemini_model").all()
    assert len(rows) == 1
    assert rows[0].value == "gemini-3.5-flash-lite"
  finally:
    session.close()


def test_settings_persist_across_separate_reads(db):
  SettingsService.set_provider("local")
  SettingsService.set_model("local", "qwen3-4b-instruct-gguf")
  assert resolve_provider() == "local"
  assert resolve_model("local") == "qwen3-4b-instruct-gguf"


# ---------------------------------------------------------------------------
# resolve_provider / resolve_model
# ---------------------------------------------------------------------------


def test_resolve_defaults_without_settings(db, monkeypatch):
  monkeypatch.delenv("LLM_PROVIDER", raising=False)
  monkeypatch.delenv("LLM_GEMINI_MODEL", raising=False)
  monkeypatch.setenv("LLM_LOCAL_MODEL_NAME", "qwen3-4b-instruct-gguf")

  assert resolve_provider() == "local"
  assert resolve_model("local") == "qwen3-4b-instruct-gguf"
  from utils.providers.gemini import DEFAULT_GEMINI_MODEL

  assert resolve_model("gemini") == DEFAULT_GEMINI_MODEL


def test_resolve_uses_settings_override(db, monkeypatch):
  monkeypatch.setenv("LLM_PROVIDER", "local")
  monkeypatch.setenv("LLM_GEMINI_MODEL", "gemini-dflt")
  monkeypatch.setenv("LLM_LOCAL_MODEL_NAME", "qwen-dflt")

  SettingsService.set_provider("gemini")
  SettingsService.set_model("gemini", "gemini-3.5-flash")

  assert resolve_provider() == "gemini"
  assert resolve_model("gemini") == "gemini-3.5-flash"
  # Unstored providers still fall back to env defaults.
  assert resolve_model("local") == "qwen-dflt"


def test_apply_to_env(db, monkeypatch):
  monkeypatch.delenv("LLM_PROVIDER", raising=False)
  monkeypatch.setenv("LLM_GEMINI_API_KEY", "test-key")
  SettingsService.set_provider("gemini")
  SettingsService.set_model("gemini", "gemini-3.5-flash")

  SettingsService.apply_to_env()
  assert os.environ["LLM_PROVIDER"] == "gemini"
  assert os.environ["LLM_GEMINI_MODEL"] == "gemini-3.5-flash"

  reset_llm()
  provider = get_llm().__class__.__name__
  assert provider == "GeminiLLMProvider"


# ---------------------------------------------------------------------------
# /select model validation (apps/cmd/main.py)
# ---------------------------------------------------------------------------


def test_select_model_unknown_provider(db):
  from apps.cmd.main import _select_model

  error = _select_model("nope", "x")
  assert "Unknown provider 'nope'" in error
  assert SettingsService.get_provider() is None


def test_select_model_gemini_missing_key(db, monkeypatch):
  from apps.cmd.main import _select_model

  monkeypatch.setenv("LLM_GEMINI_API_KEY", "")
  error = _select_model("gemini", "gemini-3.5-flash")
  assert "LLM_GEMINI_API_KEY" in error
  assert SettingsService.get_provider() is None


def test_select_model_gemini_success(db, monkeypatch):
  from apps.cmd.main import _select_model

  monkeypatch.setattr(
    "utils.env.ENV.get_gemini_api_key",
    staticmethod(lambda: "test-key"),
  )

  assert _select_model("gemini", "gemini-3.5-flash-lite") is None
  assert SettingsService.get_provider() == "gemini"
  assert SettingsService.get_model("gemini") == "gemini-3.5-flash-lite"


def test_select_model_local_missing_model(db, monkeypatch, tmp_path):
  from apps.cmd.main import _select_model

  monkeypatch.setattr(
    "utils.env.ENV.get_models_dir",
    staticmethod(lambda: str(tmp_path)),
  )

  error = _select_model("local", "qwen3-4b-instruct-gguf")
  assert "not installed" in error
  assert SettingsService.get_provider() is None


def test_select_model_zen_missing_key(db, monkeypatch):
  from apps.cmd.main import _select_model

  monkeypatch.delenv("LLM_OPENCODE_API_KEY", raising=False)
  monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
  error = _select_model("zen", "deepseek-v4-flash-free")
  assert "LLM_OPENCODE_API_KEY" in error
  assert SettingsService.get_provider() is None


def test_select_model_zen_success(db, monkeypatch):
  from apps.cmd.main import _select_model

  monkeypatch.setenv("LLM_OPENCODE_API_KEY", "zen-key-123")
  assert _select_model("zen", "deepseek-v4-flash-free") is None
  assert SettingsService.get_provider() == "zen"
  assert SettingsService.get_model("zen") == "deepseek-v4-flash-free"


# ---------------------------------------------------------------------------
# /settings output and /select chat dispatch
# ---------------------------------------------------------------------------


def test_print_settings_output(db, capsys, monkeypatch):
  from apps.cmd.main import _print_settings
  from utils.env import ENV

  SettingsService.set_provider("gemini")
  SettingsService.set_model("gemini", "gemini-3.5-flash")
  monkeypatch.setattr(ENV, "get_gemini_api_key", staticmethod(lambda: "k"))

  _print_settings()
  out = capsys.readouterr().out
  assert "Provider : gemini" in out
  assert "overridden" in out
  assert "Model    : gemini-3.5-flash" in out


def test_select_chat_dispatch_switches_and_closes_old(db, monkeypatch):
  from apps.base import Chat

  class StubAgent:
    system_prompt = "sys"

    def run(self, messages):
      return "hello"

    def take_usage(self):
      return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

  monkeypatch.setattr("apps.base._build_agent", lambda *a, **k: StubAgent())

  first = Chat.create()
  second = Chat.create()
  first_session = first.usage_session_id
  first.send("hi")

  from apps.cmd.main import _handle_select

  switched = _handle_select(first, ["/select", "chat", str(second.id)])

  assert switched is not first
  assert switched.id == second.id
  assert switched.usage_session_id is not None
  assert switched.usage_session_id != first_session

  # The old session was closed (its totals stay), the new chat has its own open one.
  from services.usage import UsageService

  assert UsageService.get_session(first_session)["ended_at"] is not None


def test_select_model_works_without_active_chat(db, monkeypatch):
  from apps.cmd.main import _handle_select

  monkeypatch.setattr(
    "utils.env.ENV.get_gemini_api_key",
    staticmethod(lambda: "test-key"),
  )

  # No chat started yet: the model switch persists without a usage session.
  result = _handle_select(None, ["/select", "model", "gemini", "gemini-3.5-flash"])
  assert result is None
  assert SettingsService.get_provider() == "gemini"
  assert SettingsService.get_model("gemini") == "gemini-3.5-flash"


def test_select_chat_without_active_chat(db, monkeypatch):
  from apps.base import Chat

  class StubAgent:
    system_prompt = "sys"

    def run(self, messages):
      return "hello"

    def take_usage(self):
      return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

  monkeypatch.setattr("apps.base._build_agent", lambda *a, **k: StubAgent())

  other = Chat.create()

  from apps.cmd.main import _handle_select

  switched = _handle_select(None, ["/select", "chat", str(other.id)])
  assert switched is not None
  assert switched.id == other.id
  assert switched.usage_session_id is not None


def test_cmd_main_inits_db_without_a_chat(monkeypatch):
  # Simulate a fresh database (no tables): main() must create them so pure
  # command sessions (/chats, /usage) don't fail with "no such table".
  from database import engine

  from utils.model import BaseModel

  from apps.cmd import main as cmd_main

  BaseModel.metadata.drop_all(engine)

  monkeypatch.setattr("sys.argv", ["cmd"])
  monkeypatch.setattr(cmd_main, "_ask", lambda: "quit")
  cmd_main.main()

  # Table exists now — the /chats path is safe.
  from apps.cmd.main import _print_chats

  _print_chats()