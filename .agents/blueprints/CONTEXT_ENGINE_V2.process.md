# CONTEXT_ENGINE_V2 — Execution Process

Process checklist for implementing **LocalMind Context Engine v2**
(blueprint: `.agents/blueprints/CONTEXT_ENGINE_V2.md`). Each task is a
checkbox; tick it off as it is finished. Keep the framework generic — never
hardcode Project/Task/EMoveX/Odoo knowledge into the core.

Project-specific notes (verified against the codebase):

- The **public chat surface must not change**: `apps/base.py::Chat`
  (`create`, `load`, `send`, `send_stream`, `history`, `context`) and the
  slash commands in `apps/cmd/main.py` (`/context`, `/global`, `/chats`,
  `/usage`) keep working while the engine replaces the internals.
- Env var is `LLM_LOCAL_CONTEXT_WINDOW` (default 4096), **not**
  `MODEL_CONTEXT_WINDOW` (renamed; see `CHANGELOG.md` and `.env.example`).
- There is **no Alembic/migration system**. `database.py::init_db()` runs
  `BaseModel.metadata.create_all(engine)`; new tables are picked up
  automatically, but new columns on existing tables are **not**. Add an
  idempotent `_ensure_column()` helper in `database.py` using
  `PRAGMA table_info(<table>)` + `ALTER TABLE ... ADD COLUMN` for `chats.state`
  and the extended `memories` columns.
- `Memory` must stay **unregistered** with `@register_model(...)`: the LLM
  reaches memories only through controlled memory tools → `MemoryService`.
- `AUTO_MEMORIZE` is the existing opt-out env for auto-capture
  (`apps/base.py:227`); Phase 0 changes the default tool set, not the flag.
- Current char-based limits to retire/replace as token budgets land:
  `MAX_CONTEXT_CHARS=12000`, `GLOBAL_CONTEXT_MAX_CHARS=4000`,
  `GLOBAL_CONTEXT_MAX_ENTRIES=12`, `TOOL_CONTEXT_TAIL_BUDGET=6000`,
  `TOOL_RESULT_TRIM_CHARS=600` (`apps/base.py`, `services/global_context.py`).
- Behavioral tests start in **Phase 1**, written alongside the engine as
  acceptance criteria — not deferred to the end.

## Phase 0 — Quick win: stop memory pollution

- [x] Shrink `AUTO_CAPTURE_TOOLS` in `apps/base.py:68` to
      `{"ask_user", "read_file"}` (remove `web_search`, `fetch_page`).
- [x] Update `.env.example` / README docs for the new default; keep
      `AUTO_MEMORIZE=0` opt-out.
- [x] Regression test: a `web_search`/`fetch_page` result during `send()` is
      never persisted into `memories` (auto-capture blocked).
- [x] Run `.venv\Scripts\python.exe -m pytest` — existing suite still green.

## Phase 1 — Context Engine (main milestone)

### 1.1 Package scaffolding

- [x] Create `services/context/` package: `engine.py`, `budget.py`,
      `retrieval.py`, `ranking.py`, `formatter.py`, `state.py` (+ `__init__`).
- [x] `ContextEngine.build(chat_id, user_message, model_context_window,
      reserved_output_tokens)` returns a `ContextPackage`; add
      `to_messages()` and `debug()`.

### 1.2 Recent conversation

- [x] Read last N user/assistant turns from `messages` (token-aware, oldest
      dropped first); expose in `ContextPackage.recent_messages`.
- [x] Assemble prompt as `system + memory + state + recent + current user`;
      `apps/base.py::Chat.send` / `send_stream` call the engine.
- [x] Streaming path (`send_stream`) still strips `<context>` and never leaks
      internal sections into the visible answer.

### 1.3 Structured chat state

- [x] Add `_ensure_column()` helper in `database.py` (`PRAGMA table_info`);
      add `chats.state` TEXT (JSON) column idempotently.
- [x] State document: objective, current_topic, decisions[], constraints[],
      open_questions[], important_entities[], last_summary.
- [x] Rewrite `CONTEXT_INSTRUCTIONS` (`apps/base.py:76-89`): the `<context>`
      block now maintains a **concise structured CHAT STATE** (condense,
      supersede, drop stale) instead of restating the entire conversation;
      cap it near ~2k chars.
- [x] Parser maps the submitted state onto the JSON document;
      `state.py` merges/persists it. `chats.context` + `get_chat_context` tool
      keep working during migration.

### 1.4 Query-aware global memory

- [x] `retrieval.retrieve_memories(query, limit)` → scored Memory entries.
- [x] Replace the static `build_global_context` snapshot in the prompt path
      (retire or shrink to a tiny permanent baseline).
- [x] Keep `search_global_memory` tool as explicit deep retrieval; automatic
      retrieval stays the cheap common case.

### 1.5 Relevant old history

- [x] Keyword-scored retrieval of old `Message` rows when the user message
      looks recall-oriented and the `retrieved` budget allows.

### 1.6 Token budgeting

- [x] `budget.py` computes budget from `LLM_LOCAL_CONTEXT_WINDOW` (fallback
      4096), reserves `max_tokens` output; char→token estimator with a
      unicode/Arabic/code safety factor.
- [x] Priority drop order: current message > constraints > state > recent >
      memories > retrieved > low-confidence. Never `context[-12000:]`.
- [x] Retire `MAX_CONTEXT_CHARS` / `GLOBAL_CONTEXT_MAX_*` /
      `TOOL_CONTEXT_TAIL_BUDGET` / `TOOL_RESULT_TRIM_CHARS` where superseded.

### 1.7 Behavioral tests (`tests/test_context_engine.py`)

- [x] T1 memory recall: prefer React Native → unrelated turns → "what mobile
      framework?" → React Native.
- [x] T2 supersession: "Use SQLite" → "switch to PostgreSQL" → "what database?"
      → PostgreSQL, never "SQLite + PostgreSQL".
- [x] T3 cross-chat: decision in Chat A found from Chat B.
- [x] T4 no pollution: web price search does not leak into "what do you
      remember about me?".
- [x] T5 budget survival: long conversation → important facts survive (no
      head-truncation).
- [x] Engine unit tests: recent messages present, budget never exceeded,
      `state` merged/persisted, `send`/`send_stream` surface unchanged.

## Phase 2 — Memory correctness

- [x] Extend `models/memory.py` via `_ensure_column()`: `confidence`,
      `status`, `superseded_by`, `source_message_id`, `last_accessed_at`,
      `access_count`.
- [x] `MemoryService.create`: detect contradictions, supersede matching active
      memory (`status="superseded"`, `superseded_by`), keep duplicate guard.
- [x] `MemoryService.search`: update `last_accessed_at`/`access_count`; rank by
      relevance × importance × confidence × recency.
- [x] `forget_memory` tool + `/memory forget N` command in `apps/cmd/main.py`.
- [x] Provenance: `source_message_id` recorded from the answered message.
- [x] Tests: T2 supersession, conflict resolution, forget, ranking change.

## Phase 3 — Retrieval quality (no vector DB)

- [x] SQLite FTS5 virtual tables over `memories.content`
      (synced on write), replacing `ilike` scans for search tools.
      (`messages.content` FTS kept out of scope: old-chat retrieval is
      keyword-scored in-memory — budgeted, small-batch.)
- [x] Hybrid ranker: FTS score + importance + confidence + recency.
- [x] Test: "User prefers simple, minimalist interfaces" found by "What UI
      style should I use?".
- [x] Embeddings/vectors explicitly out of scope (documented; no Chroma/
      Qdrant/FAISS).

## Phase 4 — Agent efficiency

- [x] `utils/agent.py`: `MAX_AGENT_STEPS` (default 8); stop on repeated
      identical tool calls (3× same name+args); bound result serialization.
- [x] Per-tool result compactors (web_search, fetch_page, read_file) replacing
      the first-600-chars slice.
- [x] Structured tool errors: `{"ok": false, "error": {"code", "message",
      "retryable"}}`.
- [x] Evaluate `ENABLE_SHELL_TOOLS`-style optional tool loading; document
      token benefit (non-blocking).

## Phase 5 — Developer tooling

- [x] `/context` debugger shows per-section token table vs budget + retrieved
      memories with scores (`ContextEngine.debug()`).
- [x] `/global` subcommands: `search`, `conflicts`, `stale`, `inspect`.
- [x] Context-budget statistics surfaced via `/usage`.
- [x] Full behavioral suite runs under `.venv\Scripts\python.exe -m pytest`.

## Phase 6 — Verification & docs

- [x] Verify Project/Task demo still works (CRUD tools unaffected).
- [x] Verify normal chats work; per-chat state persists across `load()`.
- [x] Verify streaming still works (no stray tags, no internal sections
      leaked).
- [x] Confirm real LLM call (`python main.py cmd ...`) runs without errors.
- [x] Sync `.agents/context/` (database.md, api.md, workflows.md,
      architecture.md, structure.md if layout changed).
- [x] Update AGENTS.md / README.md / CHANGELOG.md / `.env.example` for
      user-facing behavior and config changes.