# API & Interface Layer

The public contract every interface uses. Apps do NOT talk to the model
directly — they go through the `Chat` service.

## Chat service (`apps/base.py`)

Single shared gateway for all interfaces (cmd, web, api, desktop). A `Chat` is
a persisted conversation: `Chat`/`Message` rows in the SQLite DB.

- `Chat.create(title=None, system_prompt=None, max_tokens=1024)` — `ENV.init()`,
  `init_db()`, builds the `Agent` with CRUD tools, starts a new chat.
- `Chat.load(chat_id, ...)` — resume an existing chat; raises `ValueError`
  if the id is unknown.
- `chat.send(text) -> str` — the one call an interface needs. Saves the user
  message, sends `[system (+context), user]` (NOT the full history) to the LLM,
  saves the assistant reply, and returns it. On the first exchange an untitled
  chat gets a title: `_ensure_title()` asks the active LLM (`TITLE_PROMPT`,
  `tools=None`, ~24 tokens, its usage is recorded too) and falls back to the
  first user message on any failure (`_fallback_title`).
- `chat.send_stream(text) -> Generator[str, None, None]` — same as `send` but
  yields the reply in streaming chunks; the full reply is persisted once done
  (title generation runs on the completed path). Interfaces that stream print
  each chunk as it arrives.
- `chat.context` — the running conversation summary (see below).
- `chat.history` — all previous `{"role", "content"}` messages (user/assistant).
- `.tools` / `.agent` — optional access to registered tools and the agent.
- `.id` / `.title` — the persisted chat's identity.
- `chat.usage_session_id` — the currently open `Usage` session id (or `None`).
- `chat.usage_summary()` — dict with the open session, totals for this chat,
  global totals and totals by model.
- `chat.reopen_usage(provider, model)` — closes the current usage session
  (``ended_at`` set) and opens a new one for a different provider/model; used
  by ``/select model``.
- `chat.close()` — closes the usage session (`ended_at`); idempotent. Interfaces
  call this when the conversation ends (the cmd app does it in a `finally`
  after printing the summary).
- `Chat.list_chats(limit=50)` — newest-first list of dicts with `id`, `title`,
  `created_at`, `context`, `messages` (per-chat message count).

### Usage accounting

Tokens and estimated cost per chat session are stored in the `usage` table
(`models/usage.py`, `services/usage.py`). `Chat.create()`/`Chat.load()` open a
session, `send()`/`send_stream()` record the agent's accumulated
provider-reported usage on completion, and `close()` ends it. Cost comes from
`estimate_cost()` (Gemini token pricing only; local is free) — it is an
estimate, the API reports no exact bill or remaining quota. See `database.md`.

### Running chat context

The `Chat` service keeps a compact text **context** summary per chat (column
`chats.context`) instead of sending the full message history — a CPU-model
memory optimization. The context description (`CONTEXT_INSTRUCTIONS` in
`apps/base.py`, not a file) is appended to the system prompt with the current
summary, and the model is asked to append the COMPLETE updated summary as the
very last part of every answer wrapped in exactly the lowercase tags
`<context>...</context>` — and to NEVER mention/narrate the update to the user
(bookkeeping is silent, so the model just answers).

- Non-streaming: `_extract_context()` splits the raw reply into the new context
  and the visible answer; the new context is saved via `_set_context()`.
- Streaming: `_stream_strip_context()` yields the visible text while buffering
  a small tail so tags split across chunks are never shown; the extracted
  context is saved when the stream completes. An unclosed `<context>` block is
  captured rather than leaked. Tag matching is case-insensitive (free-router
  models commonly emit `<Context>`/`</CONTEXT>`).
- `_limit_context()` clamps the persisted summary to `MAX_CONTEXT_CHARS`
  (12 000) so it can never grow unbounded.
- Failed/interrupted inference persists the user message but saves no assistant
  reply or context update.
- `_system_prompt()` injects a **bounded global context** snapshot (see below)
  between the base system prompt and the chat-context instructions, plus a
  `CURRENT CHAT ID:` line so the LLM can populate `source_chat_id` on
  `save_memory` calls.
- `resources/SYSTEM_PROMPT.md` likewise instructs the model that memory saves
  and context updates are silent — never announced to the user.

The modules `Chat`/`Message` ORM classes register **read-only** for the LLM
(`create/update/delete` disabled) so only this service writes conversation data.

> Caveat: the local 4B model does not always emit the `<context>` tag on the
> first turn, so the produced context is best-effort.

## Agent (`utils/agent.py`)

Runs the tool-calling loop over a chat-completion message list (per the `Chat`
service this is the system prompt + current chat context + user message, not the
full history):

- `run(messages: list[dict]) -> str` — takes a chat-completion message list that
  already includes the system prompt and all prior turns; appends assistant
  choices and tool results as it iterates.
- `run_stream(messages: list[dict]) -> Generator[str, None, None]` — streaming
  variant that yields the plain-text answer in token chunks. Tool-calling rounds
  emit nothing (thinking + `<tool_call>` blocks are hidden); two output modes:
  Qwen3 template yields only text after the `response` marker; otherwise the
  `clean_answer()` result is emitted in chunks.
- Handles two tool-call formats: native `tool_calls` and Qwen3 GGUF
  `<tool_call>...</tool_call>` JSON blocks parsed via `parse_tool_calls()`.
- Executes tools, appends `{"role": "tool", "content": json.dumps(result)}`.
- Accumulates token usage: `_accumulate_usage()` adds the provider's `usage`
  dict (non-stream response or stream `{"usage": ...}` chunks; chunks without
  `choices` are usage-only and skipped by the stream loop).
- `take_usage()` — returns the accumulated `{prompt, completion, total}_tokens`
  for the last `run`/`run_stream` and resets it. `Chat.send()` calls it once
  streaming/non-streaming finishes and persists the totals.
- `clean_answer()` removes the model's `thinking ... response` preamble and any
  stray `response` marker line, returning only the reply.
- Loop ends when the model replies with plain text.

## Tool (`utils/tool.py`)

ABC for all tools. Each tool declares:

- `name`, `description`, `input_model` (a Pydantic model).
- `schema()` — OpenAI-style function schema from `model_json_schema()`.
- `call(arguments)` — validates via the input model, then executes.
- `execute(arguments) -> Any` — implemented by subclasses.

## LLM backend (`utils/llm.py`, `utils/providers/`)

`get_llm()` lazily builds the configured provider once and reuses it
(singleton). Persisted provider/model overrides are applied first via
`SettingsService.apply_to_env()`; `reset_llm()` clears the singleton so the
next call rebuilds it with a new selection. The provider is selected by
`LLM_PROVIDER` in `.env` (overridden by the `settings` table when set):
`local`, `gemini`, or `openai`. Providers live in `utils/providers/` and each
exposes the same OpenAI-style `create_chat_completion(messages, tools,
max_tokens, stream)` API (dict result / iterator of dict chunks), so the agent
is backend-agnostic.

- `LocalLLMProvider` — wraps the `llama-cpp-python` `Llama` singleton
  (`MODEL_CONTEXT_WINDOW`, `MODEL_CPU_THREADS`, `MODEL_GPU_LAYERS`,
  `MODEL_VERBOSE`; location via `MODELS_DIR` + `MODEL_NAME` + `model.gguf`).
  Sets `stream_marker = "response"` so the agent hides the Qwen3 thinking
  preamble while streaming.
- `GeminiLLMProvider` — online via the `google-genai` SDK
  (`GEMINI_API_KEY`, `GEMINI_MODEL`). Translates OpenAI-style messages/tool
  schemas to Gemini contents/function declarations and normalizes responses
  (text + function-call parts) back into OpenAI shape, including a `usage` key
  (`prompt_tokens`/`completion_tokens`/`total_tokens`) from
  `response.usage_metadata` on non-stream replies and a final `{"usage": ...}`
  chunk on streams. Carries Gemini 3.x `thought_signature`/`id` through
  tool-call round-trips; `stream_marker` is `None`, so text streams verbatim.
- `OpenAILLMProvider` — one OpenAI-compatible HTTP backend (httpx) serving
  BOTH free routers and Gemini (`OPENAI_MODEL`, default `openrouter/free`).
  Model ids starting with `gemini-` resolve to Gemini's OpenAI-compatible
  endpoint (`GEMINI_API_KEY`, `GEMINI_OPENAI_BASE_URL`); any other id routes to
  the free backend (`OPENAI_API_KEY`/`OPENROUTER_API_KEY`, `OPENAI_BASE_URL`,
  default `https://openrouter.ai/api/v1`) — see
  `resources/models/free_models.json` for ~140 cost-0 model ids grouped by
  router. Non-stream returns the server's OpenAI-shaped body verbatim;
  streaming parses SSE (`iter_stream_chunks`) and merges OpenRouter-style
  fragmented `tool_calls` deltas into complete calls emitted as the final
  chunk. `stream_marker` is `None`; a missing key surfaces in `_endpoint()` as
  a friendly `ValueError` when a call is actually made.

Note that when `LLM_PROVIDER=gemini` (or `openai`), `ENV.init()` only requires
`DATABASE_URL` (plus the provider's own settings) — `MODELS_DIR`/`MODEL_NAME`
are optional. Stream-mode text/tool-call output is normalized per provider in
`utils/agent.py` (marker-gated for Qwen3, verbatim for online providers).

## Runtime settings (`services/settings.py`)

`.env` holds the defaults; the `settings` table holds the runtime selection.

- `SettingsService.get/set`, `set_provider`, `set_model`, `apply_to_env`.
- `resolve_provider()` — settings override else `LLM_PROVIDER` (default
  `"local"`).
- `resolve_model(provider)` — settings override else `GEMINI_MODEL` for gemini
  (default `DEFAULT_GEMINI_MODEL`), `OPENAI_MODEL` for openai (default
  `openrouter/free`), or `MODEL_NAME` for local.
  Model env var mapping lives in `_model_env(provider)`.
- `Chat.create()`/`Chat.load()` open usage sessions with the resolved
  provider/model; the cmd app validates and persists a new choice via
  `/select model` (see `workflows.md`).

## Global memory (`services/`, `tools/memory.py`)

Cross-chat persistence as a second layer under the existing chat context:

- `MemoryService` (`services/memory.py`) — `create` (with duplicate guard:
  normalized-content match before insert), `get/update/delete`, `search` (LIKE
  over content/type, ranked by importance), `list`, `get_chat_context(chat_id)`,
  `search_chat_history(query, chat_id?, limit)`. No vector DB, no business-domain
  coupling.
- Memory tools (`tools/memory.py`) — `search_global_memory`, `get_memory`,
  `get_chat_context`, `search_chat_history`, `save_memory`. Registered alongside
  CRUD tools in `_build_agent()`. The LLM never touches the DB directly
  (tool → `MemoryService` → SQLAlchemy only).
- `services/global_context.py::build_global_context()` — builds a small
  prompt-friendly snapshot (`GLOBAL_CONTEXT_MAX_CHARS` 4000,
  `GLOBAL_CONTEXT_MAX_ENTRIES` 12): top-N most important memories grouped by type
  (preferences/decisions/topics/facts), with an optional `CURRENT CHAT ID`
  header. The full memory table is never injected into a prompt.
- `models/memory.py` — the `Memory` ORM row; **not** in the CRUD registry
  (see `database.md`).

> Adding a new interface = build a thin front-end that calls
> `Chat.create()` then `chat.send()`. Never bypass the Chat service.