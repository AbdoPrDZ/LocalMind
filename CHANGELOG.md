# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
- `Chat.send()`/`send_stream()` pass only the current chat context plus the new
  user message to the LLM (not the full history); failed inference saves no
  assistant reply or context update.
- Database location moved to `resources/data/app.db`; model layout to
  `MODELS_DIR/MODEL_NAME/model.gguf`; `.env` updated accordingly.
- Removed the demo database seed.