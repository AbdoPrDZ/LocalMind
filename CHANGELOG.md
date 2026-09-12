# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Chat titles + lazy creation: a chat row is only created for a real message
  (the cmd app no longer opens an empty chat on startup). After the first
  exchange `Chat.send()`/`send_stream()` ask the active LLM for a short title
  (`TITLE_PROMPT` in `apps/base.py`, `_ensure_title`/`_set_title`) and persist
  it to the `Chat` row, falling back to the first user message when the call
  fails. Slash commands are never persisted as messages: unknown `/` lines are
  rejected locally.
- Runtime LLM selection: `Setting` model (`models/settings.py`) + `SettingsService`
  (`services/settings.py`) persist the user's provider/model choice in the
  `settings` table; `.env` stays the default. `utils/llm.py` applies the
  override before building the provider (`apply_to_env`) and provides
  `reset_llm()` for mid-session switches. `Chat.list_chats()` /
  `Chat.reopen_usage()` support the new cmd commands: `/chats` (list chats with
  message counts), `/select chat <id>` (resume another chat, closing the
  previous usage session), `/select model <provider> <name>` (persisted switch,
  validated against `PROVIDERS`/installed models/API key), and `/settings`
  (show active provider/model and override status).
- Usage tracking: `Usage` model (`models/usage.py`) records one row per chat
  session — opened when a chat starts/resumes, closed on exit (`ended_at`),
  always referencing the chat that spent it, the provider and the model. The
  agent accumulates provider-reported tokens (`utils/agent.py`:
  `_accumulate_usage`, `take_usage`) and `Chat.send()`/`send_stream()` record
  them into the open session. Cost is an estimate (Gemini is billed per token:
  `GEMINI_PRICING_PER_1M` in `services/usage.py`; local runs are tracked but
  free). `UsageService` (`services/usage.py`) provides `start_session`, `record`,
  `close_session`, totals, totals per chat and totals per model. Interfaces call
  `Chat.close()` on exit; the cmd app prints a usage summary (session + global)
  and `scripts/usage_report.py` renders full per-chat/per-model reports.
- Online LLM backends: `LLM_PROVIDER` in `.env` switches between `local`
  (llama-cpp, default) and `gemini` (Google `google-genai` SDK). New provider
  layer in `utils/providers/` (`LLMProvider` contract, `LocalLLMProvider`,
  `GeminiLLMProvider`) exposing an OpenAI-style chat-completion API, so the
  agent's tool-calling loop works unchanged with either backend. Gemini
  settings: `GEMINI_API_KEY`, `GEMINI_MODEL`; `MODELS_DIR`/`MODEL_NAME` are no
  longer required when using an online provider. Gemini 3.x `thought_signature`
  round-trips are preserved across tool calls (`utils/providers/gemini.py`),
  and tool results now carry `name`/`tool_call_id` for remote providers.
  Agent streaming supports preamble-free providers (verbatim token streaming)
  alongside the existing Qwen3 marker gating (`utils/agent.py`).
- Global memory system: `Memory` model (`models/memory.py`, cross-chat
  memories with type/importance/provenance), `MemoryService`
  (`services/memory.py`), memory tools (`tools/memory.py`:
  `search_global_memory`, `get_memory`, `get_chat_context`,
  `search_chat_history`, `save_memory`), and a bounded global-context snapshot
  (`services/global_context.py`) injected into the system prompt. Duplicate
  guard via normalized-content matching.
- Model installer script `scripts/install_model.py`: downloads GGUF models from
  Hugging Face into `resources/models/<name>/model.gguf`, backed by the model
  registry (`resources/models/registry.json`), with `--list` and `--all`
  options.
- Test suite with `pytest` (`tests/`): memory persistence, search/ranking,
  duplicate guard, provenance, chat-context/history retrieval, bounded global
  context, and tool argument validation.
- `apps/cmd` interface: interactive `questionary` CLI and single-shot prompt mode.
- `Chat` / `Message` ORM models and the `Chat` service in `apps/base.py`: a
  persisted conversation (user + assistant messages in SQLite) that
  `chat.send(text)` drives against the LLM.
- Running chat context: `chats.context` column holds a compact conversation
  summary that is sent instead of the full history; the model emits the updated
  summary in `<context>...</context>` tags which are extracted, persisted and
  stripped from the reply (`_extract_context`, `_stream_strip_context`,
  `_limit_context`). Context is capped at `MAX_CONTEXT_CHARS`.
- Streaming answers: `Agent.run_stream()`, `Chat.send_stream()`, and the cmd
  app now print the reply token-by-token as it is generated.
- Root `main.py` dispatcher with argparse `app` positional;
  `cmd` implemented, `web`/`api`/`desktop` raise `NotImplementedError`.
- Project context in `.agents/context/` and agent rules in `AGENTS.md`.
- `README.md` and this `CHANGELOG.md`.

### Changed

- Replaced the old `LLMBridge` with the `Chat` service; `Agent.run()` now takes
  a full message history, and the cmd app supports `--chat <id>` to resume.

### Fixed

- The cmd app now calls `init_db()` at startup. Lazy chat creation meant an
  all-commands session (e.g. `/chats` on a fresh/removed database) no longer
  forced table creation and crashed with `no such table: messages`.
- `Chat.send()`/`send_stream()` pass only the current chat context plus the new
  user message to the LLM (not the full history); failed inference saves no
  assistant reply or context update.
- Database location moved to `resources/data/app.db`; model layout to
  `MODELS_DIR/MODEL_NAME/model.gguf`; `.env` updated accordingly.
- Removed the demo database seed.
