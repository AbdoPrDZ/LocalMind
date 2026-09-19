# API & Interface Layer

The public contract every interface uses. Apps do NOT talk to the model
directly — they go through the `Chat` service.

## Chat service (`apps/base.py`)

Single shared gateway for all interfaces (cmd, web, api, desktop). A `Chat` is
a persisted conversation: `Chat`/`Message` rows in the SQLite DB.

- `Chat.create(title=None, system_prompt=None, max_tokens=1024, question_handler=None)` — `ENV.init()`,
  `init_db()`, builds the `Agent` with the full tool set, starts a new chat.
- `Chat.load(chat_id, ..., question_handler=None)` — resume an existing chat; raises `ValueError`
  if the id is unknown.

`question_handler` is the app's blocking question callback
`(question, options: list[str] | None, allow_free_text: bool) -> str | None`
(passes through `_build_agent` to the tools that need it — see `tools/ask.py`).
cmd supplies `_ask_value` (questionary); web/api/desktop inject their own.
Returning `None` means the user dismissed the prompt.
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

The context **accumulates, it never replaces**: `CONTEXT_INSTRUCTIONS` tells
the model to preserve every earlier topic and append the new exchange in order
(`The chat started with ..., then ...`), and `_set_context()` enforces it via
`_merge_contexts()` — an update that already contains the full stored context
trusts the update; otherwise the new topic is appended and exact-duplicate
lines dropped. A model overwriting the summary with just the latest topic no
longer erases the conversation.

- Non-streaming: `_extract_context()` splits the raw reply into the new context
  and the visible answer; the new context is merged/saved via `_set_context()`.
- Streaming: `_stream_strip_context()` yields the visible text while buffering
  a small tail so tags split across chunks are never shown; the extracted
  context is saved when the stream completes. An unclosed `<context>` block is
  captured rather than leaked. Tag matching is case-insensitive (free-router
  models commonly emit `<Context>`/`</CONTEXT>`).
- `_limit_context()` clamps the persisted summary to `MAX_CONTEXT_CHARS`
  (12 000) so it can never grow unbounded.
- When the model sends **no `<context>` block**, the service falls back to the
  agent's recorded `tool_results`: `_synthesize_tool_context()` folds them into
  a "RECENT TOOL RESULTS" section (skipping memory/system/clipboard reads,
  ~6000-char budget) saved as the new context — tool knowledge is not lost on
  turns where the model skips the tags. Durable tools (`read_file`, `ask_user`
  only — `fetch_page`/`web_search` results are deliberately NOT captured) are
  additionally auto-captured as low-importance global facts by
  `_capture_global_memories()` (content truncated to 1200 chars, deduplicated by
  the memory service; `AUTO_MEMORIZE=1` in `.env`, `0` disables; failures
  swallowed) with the triggering user message recorded as `source_message_id`.
- Failed/interrupted inference persists the user message but saves no assistant
  reply or context update.
- The prompt is assembled by the **ContextEngine** (`services/context/`):
  `_engine_messages(user_message)` builds a `system` message (base prompt +
  query-aware relevant global memories + CHAT STATE + protocol instructions) and
  the most recent verbatim turns as user/assistant messages, packed to the token
  budget derived from `LLM_LOCAL_CONTEXT_WINDOW` (output reserve
  `agent.max_tokens`/1024). `_system_prompt()` is legacy/unused by `send()`.
- `resources/SYSTEM_PROMPT.md` likewise instructs the model that memory saves
  and context updates are silent — never announced to the user, and durable
  profile/world facts should be saved with `save_memory`.

The modules `Chat`/`Message` ORM classes register **read-only** for the LLM
(`create/update/delete` disabled) so only this service writes conversation data.

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
  stray `response` marker line, returning only the reply. It also strips
  `<thinking>...</thinking>`, `<|im_start|>think ... <|im_start|>answer ...`
  and bare `thinking`/`response` marker lines (case-insensitive) so reasoning
  never leaks into the visible answer.
- Records every executed tool result: `self.tool_results` (reset at the start
  of each `run`/`run_stream`, skipping results with an `"error"` top key or a
  structured `ok: False` error envelope); `take_tool_results()` returns-and-clears
  so `Chat.send()` can persist them without re-triggering.
- Guardrails: the loop stops after `MAX_AGENT_STEPS` iterations (default 8, env
  `MAX_AGENT_STEPS`), a repeated identical `name+arguments` call (3×) aborts with
  `LOOP_LIMIT_ANSWER`, and each tool result serialized back to the model is
  bounded to `MAX_TOOL_RESULT_CHARS` (6000) with a truncation marker.
- Unknown tools return a structured error envelope
  `{"ok": false, "error": {"code", "message", "retryable"}}` (`utils.tool.tool_error`).
- Loop ends when the model replies with plain text.

## Tool (`utils/tool.py`)

ABC for all tools. Each tool declares:

- `name`, `description`, `input_model` (a Pydantic model).
- `schema()` — OpenAI-style function schema from `model_json_schema()`.
- `call(arguments)` — validates via the input model, then executes.
- `execute(arguments) -> Any` — implemented by subclasses.
- `tool_error(code, message, retryable)` — structured error envelope helper.

## Tool builders (`tools/`)

`_build_agent` assembles every tool call in `apps/base.py`:

- `build_crud_tools()` — generic per-model CRUD (`tools/model.py`).
- `build_memory_tools()` — the six memory tools (`tools/memory.py`):
  `search_global_memory`, `get_memory`, `get_chat_context`,
  `search_chat_history`, `save_memory`, `forget_memory`.
- `build_files_tools()` — `read_file`/`write_file`/`list_dir` scoped to the
  `ALLOWED_PLACES` folders (`tools/files.py`); each schema's `place` field is
  a `Literal` of configured place names and every resolved path is
  containment-checked.
- `build_web_tools()` — keyless `web_search` (Bing RSS) + `fetch_page`
  (`tools/web.py`); HTTP-only, httpx + stdlib parsing.
- `build_system_tools()` — `current_datetime` (IANA/local; needs `tzdata`),
  `get_system_info`, Windows clipboard get/set (`tools/system.py`).
- `build_ask_tools(handler)` — `ask_user`: asks the user via the injected
  handler; no handler or a dismissed prompt → `{"error": ...}` to the model.
- `build_shell_tools(handler)` — `run_command`: off unless
  `ENABLE_SHELL_TOOLS=1`; requires a short `description`; asks the user to
  approve ("Yes, run it" / "No, cancel") via the handler before executing;
  `cwd` must be inside an allowed folder (`tools/shell.py`).

## LLM backend (`utils/llm.py`, `utils/providers/`)

`get_llm()` lazily builds the configured provider once and reuses it
(singleton). Persisted provider/model overrides are applied first via
`SettingsService.apply_to_env()`; `reset_llm()` clears the singleton so the
next call rebuilds it with a new selection. The provider is selected by
`LLM_PROVIDER` in `.env` (overridden by the `settings` table when set):
`local`, `gemini`, `openai`, `free`, or `zen`. Providers live in `utils/providers/`
and each exposes the same OpenAI-style `create_chat_completion(messages, tools,
max_tokens, stream)` API (dict result / iterator of dict chunks), so the agent
is backend-agnostic.

- `LocalLLMProvider` — wraps the `llama-cpp-python` `Llama` singleton
  (`LLM_LOCAL_CONTEXT_WINDOW`, `LLM_LOCAL_CPU_THREADS`, `LLM_LOCAL_GPU_LAYERS`,
  `LLM_LOCAL_VERBOSE`; location via `LLM_LOCAL_MODELS_DIR` +
  `LLM_LOCAL_MODEL_NAME` + `model.gguf`).
  Sets `stream_marker = "response"` so the agent hides the Qwen3 thinking
  preamble while streaming.
- `GeminiLLMProvider` — online via the `google-genai` SDK
  (`LLM_GEMINI_API_KEY`, `LLM_GEMINI_MODEL`). Translates OpenAI-style
  messages/tool schemas to Gemini contents/function declarations and normalizes
  responses (text + function-call parts) back into OpenAI shape, including a
  `usage` key (`prompt_tokens`/`completion_tokens`/`total_tokens`) from
  `response.usage_metadata` on non-stream replies and a final
  `{"usage": ...}` chunk on streams. Carries Gemini 3.x
  `thought_signature`/`id` through tool-call round-trips; `stream_marker` is
  `None`, so text streams verbatim.
- `OpenAILLMProvider` — one OpenAI-compatible HTTP backend (httpx) serving
  BOTH free routers and Gemini (`LLM_OPENAI_MODEL`, default `openrouter/free`).
  Model ids starting with `gemini-` resolve to Gemini's OpenAI-compatible
  endpoint (`LLM_GEMINI_API_KEY`, `LLM_GEMINI_OPENAI_BASE_URL`); any other id
  routes to the free backend (`LLM_OPENAI_API_KEY`/`LLM_OPENROUTER_API_KEY`,
  `LLM_OPENAI_BASE_URL`, default `https://openrouter.ai/api/v1`) — see
  `resources/models/free_models.json` for ~140 cost-0 model ids grouped by
  router. Non-stream returns the server's OpenAI-shaped body verbatim;
  streaming parses SSE (`iter_stream_chunks`) and merges OpenRouter-style
  fragmented `tool_calls` deltas into complete calls emitted as the final
  chunk. `stream_marker` is `None`; a missing key surfaces in `_endpoint()` as
  a friendly `ValueError` when a call is actually made.
- `FreeLLMProvider` — keyless OpenAI-compatible HTTP client
  (`utils/providers/free.py`, `LLM_PROVIDER=free`): a hosted free model with no
  API key at all. Endpoint + model resolved from
  `resources/models/keyless_models.json` via `resolve_endpoint()`/`available_models()`
  (`LLM_FREE_ENDPOINT`/`LLM_FREE_MODEL`, default `pollinations` / `openai-fast`;
  `LLM_FREE_BASE_URL` overrides). Sends NO `Authorization` header; reuses the
  `openai` provider's `_chat_url`/`iter_stream_chunks`/`_error_text` so SSE tool
  calls and error formatting are identical. `stream_marker` is `None`. Registered
  in the factory (`PROVIDERS["free"]`), wired into `/select model free <model>`,
  `/settings`, and `services/settings.py` (`LLM_FREE_MODEL` env mapping).
  Experimental: free endpoints rate-limit and can inject promotional notices.
  A **notice-guard** (`_is_notice`/`_guarded` in `free.py`) detects injected
  promo/budget boilerplate (`_NOTICE_MARKERS` — e.g. Pollinations' "raise the
  key budget"), retries once with a `_RETRY_NUDGE` system message, and raises
  `_notice_error(...)` if the endpoint still advertises — a polluted reply is
  never surfaced to the user. Streaming is buffered through the guard, so
  streamed output is validated before it is replayed as chunks.
- `ZenLLMProvider` — OpenCode Zen gateway
  (`utils/providers/zen.py`, `LLM_PROVIDER=zen`): a thin subclass of
  `OpenAILLMProvider` pointing the same HTTP/SSE machinery at
  `https://opencode.ai/zen/v1` (default; `LLM_ZEN_BASE_URL` overrides) with one
  API key (`LLM_OPENCODE_API_KEY`, fallback `OPENCODE_API_KEY`;
  `LLM_ZEN_MODEL`, default `DEFAULT_ZEN_MODEL` `deepseek-v4-flash-free`, a free
  tier; ids from https://opencode.ai/zen/v1/models). `_endpoint()` always
  returns the Zen base url/key — no `gemini-` special-casing — and raises a
  friendly `ValueError` when the key is missing. Native `tool_calls`, SSE
  streaming and error formatting are inherited unchanged; `stream_marker` is
  `None`. Not every catalog family works: the `gpt-*` Responses-API models and
  native Anthropic/Google endpoints aren't served through
  `/v1/chat/completions`. Zen requires an `x-opencode-session` header on every
  request; the provider sends it when `LLM_ZEN_SESSION_ID` (or
  `OPENCODE_SESSION_ID`) is set — a real OpenCode session id also unlocks the
  **free-tier ids** (`*-free`, `big-pickle`, ...) outside the OpenCode app.
  When free-tier is rejected anyway, `_explain()` detects those bodies
  (`MissingSessionID`/unavailable) and appends actionable guidance.
  Usage tokens count; cost is estimated only for `gemini-*` ids (Zen prices
  match LocalMind's Gemini table, no markup).

Note that when `LLM_PROVIDER=gemini` (or `openai`/`free`/`zen`), `ENV.init()` only
requires `DATABASE_URL` (plus the provider's own settings) —
`LLM_LOCAL_MODELS_DIR`/`LLM_LOCAL_MODEL_NAME` are optional. Stream-mode
text/tool-call output is normalized per provider in `utils/agent.py`
(marker-gated for Qwen3, verbatim for online providers).

## Runtime settings (`services/settings.py`)

`.env` holds the defaults; the `settings` table holds the runtime selection.

- `SettingsService.get/set`, `set_provider`, `set_model`, `apply_to_env`.
- `resolve_provider()` — settings override else `LLM_PROVIDER` (default
  `"local"`).
- `resolve_model(provider)` — settings override else `LLM_GEMINI_MODEL` for
  gemini (default `DEFAULT_GEMINI_MODEL`), `LLM_OPENAI_MODEL` for openai
  (default `openrouter/free`), `LLM_FREE_MODEL` for free (default
  `DEFAULT_FREE_MODEL`), `LLM_ZEN_MODEL` for zen (default `DEFAULT_ZEN_MODEL`),
  or `LLM_LOCAL_MODEL_NAME` for local.
  Model env var mapping lives in `_model_env(provider)`.
- `Chat.create()`/`Chat.load()` open usage sessions with the resolved
  provider/model; the cmd app validates and persists a new choice via
  `/select model` (see `workflows.md`).

## Context engine (`services/context/`)

Assembly of what the model actually sees each turn, decoupled from persistence
(`chats.state` JSON):

- `engine.py` `ContextEngine(chat, memories, prefer_history=False)` — builds a
  `ContextPackage` (system/user/assistant messages) at `build(send_extra)`
  under a `token_budget`, and offers `debug()` for the `/context` debugger.
- `budget.py` — converts the model context window + output reserve into the
  system/turn budgets (per-turn char allowances, `summarized_len` vs
  `verbatim_len`).
- `state.py` — parse/merge/serialize/summarize of the structured chat state
  (`topics`, `summary`, `pending`); `summarize_for_tokens`.
- `retrieval.py` + `ranking.py` — retrieve old-chat snippets (via
  `search_chat_history`) and global memories relevant to the new message.
- `formatter.py` — renders topics/summary to compact text; `CURRENT CHAT ID`
  header is included here. The legacy snapshot path
  (`services/global_context.py::build_global_context`) remains for `/global`
  display only.

## Global memory (`services/`, `tools/memory.py`)

Cross-chat persistence as a second layer under the existing chat context:

- `MemoryService` (`services/memory.py`) —
  `create` (duplicate guard + boilerplate-aware subject supersession),
  `get`, `update` (does NOT supersede — keep that signal for `conflicts()`),
  `archive` via `forget(id)`/`forget_by_query(q)`, `search` (FTS5 candidates
  when the `memories_fts` index exists and `database.is_fts_available()`, ilike
  fallback otherwise; both re-ranked by keyword overlap × importance ×
  confidence and bump `access_count`/`last_accessed_at`),
  `list(status=active)`, `get_chat_context(chat_id)`,
  `search_chat_history(query, chat_id?, limit)` (matched for the engine),
  `conflicts()`, `stale(days)` (inspection for `/global conflicts|stale`).
  No vector DB, no business-domain coupling.
- Memory tools (`tools/memory.py`) — `search_global_memory`, `get_memory`,
  `get_chat_context`, `search_chat_history`, `save_memory`, `forget_memory`;
  failures are returned as `tool_error(...)` envelopes (never exceptions).
  Registered alongside CRUD tools in `_build_agent()`. The LLM never touches the
  DB directly (tool → `MemoryService` → SQLAlchemy only).
- `services/global_context.py::build_global_context()` — builds a small
  prompt-friendly snapshot (`GLOBAL_CONTEXT_MAX_CHARS` 4000,
  `GLOBAL_CONTEXT_MAX_ENTRIES` 12): top-N most important memories grouped by type
  (preferences/decisions/topics/facts), with an optional `CURRENT CHAT ID`
  header. Retained for the `/global` display; the engine performs its own
  query-aware retrieval.
- `models/memory.py` — the `Memory` ORM row; **not** in the CRUD registry
  (see `database.md`).

> Adding a new interface = build a thin front-end that calls
> `Chat.create()` then `chat.send()`. Never bypass the Chat service.