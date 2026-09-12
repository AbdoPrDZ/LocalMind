# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- OpenAI-compatible `openai` LLM provider (`utils/providers/openai.py`): one
  backend that serves both free online models and Gemini. Model ids starting
  with `gemini-` call Gemini's OpenAI-compatible endpoint
  (`GEMINI_API_KEY`, `GEMINI_OPENAI_BASE_URL`); everything else routes to the
  free router configured by `OPENAI_BASE_URL` (default OpenRouter) using
  `OPENAI_API_KEY`/`OPENROUTER_API_KEY`. It speaks OpenAI chat completions over
  httpx in both non-streaming and SSE streaming form, merges OpenRouter-style
  fragmented streaming `tool_calls` back into complete calls, and is driven by
  `OPENAI_MODEL` (default `openrouter/free`).
- Free model catalog at `resources/models/free_models.json`: ~140 cost-0 model
  ids copied from opencode's own model registry, grouped by router (bothub,
  openrouter, kilo, unorouter, orcarouter, tokenrouter, zenmux, kenari,
  aihubmix, nvidia), each with its API base url and env key. Paid-by-subscription
  cost-0 tiers (e.g. openrouter `auto`/`fusion`, kenari non-free entries) are
  excluded. Tracked in git (`.gitignore` exception next to `registry.json`).
- `httpx` added to `requirements.txt` (already a transitive dep of `google-genai`).
- Usage accounting: Gemini models used through the `openai` provider are now
  billed with Gemini pricing; free routers stay cost 0
  (`estimate_cost` in `services/usage.py`).
- New tool sets, always wired into the agent but safely gated:
  - File tools (`tools/files.py`): `read_file`, `write_file`, `list_dir`
    scoped to the `ALLOWED_PLACES` folders (`.env`, default
    `workspace=./workspace`). Every operation carries a `place` parameter
    constrained to the configured folder names; paths are containment-checked
    (realpath vs configured roots) so the LLM cannot escape them.
  - Web tools (`tools/web.py`): keyless `web_search` (Bing RSS) and
    `fetch_page` (httpx + stdlib HTML→text extraction), no API key required.
  - System tools (`tools/system.py`): `current_datetime` (IANA or local,
    `tzdata` added to requirements), `get_system_info` (platform; `psutil`
    only when installed), `clipboard_get`/`clipboard_set` (PowerShell, Windows).
  - Shell tool (`tools/shell.py`): `run_command` runs a command only after the
    user approves it — gated by `ENABLE_SHELL_TOOLS=1`, requires a short
    `description`, asks via the app's question handler ("Yes, run it" / "No,
    cancel"), and confines `cwd` to the allowed folders.
  - Ask-user tool (`tools/ask.py`): generic `ask_user` — each interface injects
    its own blocking question handler `(question, options, allow_free_text) ->
    answer | None`; without a handler (or when the user dismisses) the tool
    reports an error to the model instead of crashing.
- Notes model (`models/note.py`): a registered CRUD store (title, content,
  tags, timestamps) served by the same generic `create/get/list/update/delete`
  tools — no tool is specialized for it.
- `tzdata` added to `requirements.txt` (IANA timezones on Windows).
- Tool knowledge is now **remembered for you**:
  - The agent records every tool result it executes
    (`tool_results` in `utils/agent.py`, `take_tool_results()`), and the `Chat`
    service auto-folds them into the chat context as a "RECENT TOOL RESULTS"
    section whenever the model emits no `<context>` block of its own
    (`_synthesize_tool_context` in `apps/base.py`) — so fetched pages, search
    results, and file reads stop being lost between turns.
  - Durable tool findings (`fetch_page`, `web_search`, `read_file`,
    `ask_user`) are also auto-captured as low-importance `fact` global memories
    (`_capture_global_memories`, gated by `AUTO_MEMORIZE=1` in `.env`, disabled
    with `0`; content truncated to 1200 chars and deduplicated by the memory
    service) — so the profile you had it fetch once survives into other chats.
  - `Agent.clean_answer()` hardened against leaked reasoning for both tag
    styles now strip `<thinking>...</thinking>`, `<|im_start|>think ... /
    <|im_start|>answer ... / response`, and bare `thinking ... response` /
    `response ...` marker preambles (case-insensitive).
- `AUTO_MEMORIZE` added to `.env.example`.
- Keyless `free` LLM provider (`utils/providers/free.py`, `LLM_PROVIDER=free`):
  interact with a hosted free model with **no API key at all**. Endpoint and
  model come from a new registry `resources/models/keyless_models.json`
  (`FREE_ENDPOINT`/`FREE_MODEL`, default Pollinations.ai anonymous tier +
  `openai-fast`; `FREE_BASE_URL` overrides the whole endpoint). It is a thin
  keyless `chat/completions` HTTP client that reuses the `openai` provider's
  URL joining, SSE parsing (incl. tool-call merging) and error formatting — the
  agent/tool loop is untouched. Registered in the provider factory
  (`utils/providers/__init__.py`), integrated with `/select model free <model>`
  and `/settings`; no fallback or auto-switching, matching the "you pick the
  endpoint" design.
  - **Notice-guard**: keyless tiers sometimes answer with injected promotional
    "raise the key budget" spam (empirically confirmed on the anonymous
    Pollinations tier — it currently injects this on every reply). The provider
    detects that boilerplate, retries once with a "don't advertise" nudge, and
    if the endpoint still advertises, raises a clear error pointing at
    `FREE_MODEL`/`FREE_ENDPOINT`/`LLM_PROVIDER` — a polluted reply is never
    shown to the user. Verified live: `keylessai.thryx.workers.dev` is DNS-dead
    and `api.airforce` now requires a paid balance/Authorization, so
    Pollinations' anonymous tier is the only live keyless endpoint left and the
    free provider intentionally stays experimental.

### Fixed

- `openai` provider joined the base URL without a separator
  (`.../api/v1chat/completions` → HTTP 404); `_chat_url()` now normalizes the
  trailing slash for both the free router and Gemini endpoints.
- Streaming tag-strip in `apps/base.py` (`_stream_strip_context`) matched the
  `<context>` tags only case-sensitively, so mixed-case tags (common with
  free-router models) leaked into the visible answer; matching is now
  case-insensitive.
- Crashes persisting text with lone surrogates (Windows console input can
  carry unpaired UTF-16 units) — `UnicodeEncodeError: 'utf-8' codec can't
  encode ... surrogates not allowed` on the message INSERT. New `_clean_text()`
  in `apps/base.py` strips invalid units (valid emoji untouched) before every
  write: user/assistant messages, chat context, titles, and auto-captured
  memories.
- Misleading free-router error message: the URL was printed without the joining
  slash (`.../api/v1chat/completions`) while the real request was correct —
  `_error_text()` in `utils/providers/openai.py` now shows the joined URL and
  appends an actionable hint for HTTP 429 (daily free quota exhausted: wait for
  reset / add credits / switch to `LLM_PROVIDER=local` or `gemini`).
- The cmd app traceback-crashed on any provider error (e.g. a 429 rate limit);
  `_print_reply()` in `apps/cmd/main.py` now prints a one-line error and keeps
  the interactive loop alive (the user message stays persisted either way).
- Knowledge loss between turns and chats: when the model sent no `<context>`
  block, tool results (e.g. a fetched GitHub profile) only existed inside that
  one reply and vanished afterwards — tool outputs are now folded into the chat
  context automatically and durable ones captured as global facts. The chat
  context itself could also be overwritten when the model summarized only the
  newest topic; it now merges and accumulates instead of replacing.

### Changed

- The model is now told (in `resources/SYSTEM_PROMPT.md` and the chat-context
  instructions in `apps/base.py`) that chat-context updates and memory saves
  are **silent internal bookkeeping**: it must never announce them to the user,
  must wrap the updated context in exactly the lowercase `<context>...</context>`
  tags, and must not wrap them in code fences. The model just answers.
- The CHAT CONTEXT **accumulates, it never replaces**: the model is instructed
  to emit the whole conversation in order and `_set_context()` merges through
  `_merge_contexts()` — an update already containing the stored context replaces
  it, otherwise the new topic is appended and duplicate lines dropped — so a
  weak model overwriting the summary with the latest topic no longer erases
  what came before.

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
