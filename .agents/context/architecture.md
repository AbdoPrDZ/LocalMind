# Architecture

## What this project is

`LocalMind` is a **platform for talking to a local LLM through multiple
interfaces**. One shared core, several front-ends: `cmd` (done), with `web`,
`api` and `desktop` planned. The LLM runs entirely offline via
`llama-cpp-python` and can call generic tools.

> The `projects`/`tasks` models and their CRUD tools are **example
> scaffolding** — they demonstrate that we can generate tools dynamically from
> models. They are NOT the goal of the project.

## Layers

```text
interfaces/  (apps/)           cmd (done), web, api, desktop (planned)
    │  Chat.create() / Chat.load() / chat.send()
shared service (apps/base.py)  Chat: ENV init, DB init, agent build,
                               persists user+assistant messages + a compact
                               chat context (not full history)
    │
agent        (utils/agent.py)  tool-calling loop, parses model output
    │
tools        (tools/model.py)  generic CRUD tools generated from registry
    │        (tools/memory.py)  controlled global-memory tools
    │        (tools/files.py)   read/write/list scoped to ALLOWED_PLACES folders
    │        (tools/web.py)     keyless web_search (Bing RSS) + fetch_page
    │        (tools/system.py)  current_datetime, system info, clipboard
    │        (tools/shell.py)   run_command — user-approval gated
    │        (tools/ask.py)     ask_user — generic, per-app question handler
    │
services     (services/context/)  ContextEngine: token-budgeted prompt assembly
                                   (recent turns + structured chat state +
                                   query-aware global memory / old-chat history)
              (services/memory.py) MemoryService; memory lifecycle (status,
                                   supersession, forget) + FTS5 hybrid search
              (services/global_context.py) legacy global-context builder
              (services/usage.py)  UsageService; token/cost accounting per session
              (services/settings.py) SettingsService; runtime provider/model
                                   overrides (settings table, `.env` is default)
    │
LLM backend  (utils/llm.py)    provider factory (get_llm) over
             (utils/providers/)  local llama-cpp OR Gemini OR an OpenAI-
                                  compatible backend (free routers + Gemini)
                                  OR a keyless "free" hosted endpoint
                                  (all speak the OpenAI-style API)
    │
database     (database.py)     SQLAlchemy + SQLite
```

## Data flow (chat)

1. An interface calls `Chat.create()` (or `Chat.load(id)`), then `chat.send(text)`.
   Creating/resuming a chat opens a `Usage` session (`UsageService.start_session`)
   that records the provider and model; the interface calls `chat.close()` on
   exit to stamp `ended_at`.
2. `send()` saves the user message, then asks the **ContextEngine**
   (`services/context/engine.py`) to assemble the prompt: `system` =
   base prompt + relevant global memories + structured CHAT STATE + protocol
   instructions, plus the most recent verbatim turns as messages — all packed
   under a token budget derived from `LLM_LOCAL_CONTEXT_WINDOW`. The full
   message history is NOT sent; the engine reads it from the DB.
3. The agent sends the messages + tool schemas (CRUD, memory, **and** the
   scoped files/web/system/ask tools) to the model.
4. If the model asks for tools, the agent executes them and feeds results back
   (results are bounded in size; guardrails cap loop steps at `MAX_AGENT_STEPS=8`
   and abort repeated identical calls). `ask_user` blocks on the interface's
   question handler; `run_command` blocks on the user's explicit approval before
   executing anything.
5. Repeats until the model produces a plain-text answer.
6. Context persistence: a `<context>...</context>` block in the reply is
   extracted and merged into the stored chat context — `_set_context` writes the
   JSON chat state into `chats.state` (mirrored as text in the legacy
   `chats.context`) via the engine's `parse_state`/`merge_summary`/`serialize`.
   If the model sent no block, the agent's recorded tool results are folded in
   automatically (`_synthesize_tool_context`, per-tool compactors) and durable
   tools (`read_file`, `ask_user` — **not** web tools) are captured as
   low-importance global facts with `source_message_id` provenance
   (`_capture_global_memories`, gated by `AUTO_MEMORIZE=1`). `send()` saves the
   cleaned assistant reply and returns it.
7. The agent accumulates provider-reported token usage (`take_usage()`); each
   `send()`/`send_stream()` records it into the open session. Cost is estimated
   per token (Gemini pricing in `services/usage.py`; local is tracked but free).

Global memory sits under the per-chat context: memories persist across chats
(`services/memory.py`, `models/memory.py`). Each entry carries importance,
confidence, a lifecycle `status` (active/superseded/archived), `superseded_by`,
`source_chat_id`/`source_message_id` provenance, and access stats. `MemoryService`
handles duplicate guard, subject-based auto-supersession on create, archive via
`forget`, and a hybrid ranker (SQLite **FTS5** `memories_fts` index OR ilike
fallback × importance × confidence). The model reaches memories only through the
controlled tools (`search_global_memory`, `get_memory`, `get_chat_context`,
`search_chat_history`, `save_memory`, `forget_memory`).

## Tool safety model

The agent's extra tools never touch things the user hasn't scoped to it:

- **Files** are confined to `ALLOWED_PLACES` (`.env`, default
  `workspace=./workspace`, comma-separated `name=path`). Each file tool's input
  schema has a `place` field restricted to a `Literal` of configured names; the
  resolved path is containment-checked (realpath within a configured root) and
  any escape attempt is refused.
- **Shell** (`run_command`) is inert unless `ENABLE_SHELL_TOOLS=1`. The model
  must also provide a short `description`, and the tool asks the user for
  explicit approval ("Yes, run it" / "No, cancel") via the interface's question
  handler before executing; `cwd` is resolved among the allowed folders.
- **Ask** (`ask_user`) is generic: `_build_agent` receives a
  `question_handler` — a blocking `(question, options, allow_free_text) ->
  answer | None` callback supplied by each interface (cmd uses questionary;
  web/api/desktop will pass their own). Without a handler, or when the user
  dismisses, the tool returns an error to the model instead of crashing.
  `Chat.create()` / `Chat.load()` accept and forward this handler.
- Web/system tools are read-only and keyless (Bing RSS search, stdlib HTML→text,
  platform info, time, clipboard via PowerShell).

## Configuration

All runtime config lives in `.env` at the project root (loaded by
`utils/env.py`). `DATABASE_URL` is always required. `LLM_PROVIDER` picks the
backend: `local` (default) additionally requires `LLM_LOCAL_MODELS_DIR`,
`LLM_LOCAL_MODEL_NAME`; `gemini` uses `LLM_GEMINI_API_KEY` and optional
`LLM_GEMINI_MODEL` (online); `openai` serves free routers AND Gemini through
one OpenAI-compatible interface — model ids starting with `gemini-` go to
Gemini (`LLM_GEMINI_API_KEY`, `LLM_GEMINI_OPENAI_BASE_URL`), anything else
routes to the free backend (`LLM_OPENAI_API_KEY`/`LLM_OPENROUTER_API_KEY`,
`LLM_OPENAI_BASE_URL` default `https://openrouter.ai/api/v1`,
`LLM_OPENAI_MODEL` default `openrouter/free`; ~140 free model ids in
`resources/models/free_models.json`). `LLM_PROVIDER=free` uses a keyless
endpoint selected by `LLM_FREE_ENDPOINT`/`LLM_FREE_MODEL` (or overridden by
`LLM_FREE_BASE_URL`). `LLM_PROVIDER=zen` routes through OpenCode Zen's
OpenAI-compatible gateway (`LLM_OPENCODE_API_KEY`, `LLM_ZEN_MODEL` default a
free tier, `LLM_ZEN_BASE_URL` default `https://opencode.ai/zen/v1`) — a thin
subclass of the `openai` provider (see `api.md`). Locally, the per-backend model vars are
`LLM_LOCAL_CONTEXT_WINDOW`, `LLM_LOCAL_CPU_THREADS`, `LLM_LOCAL_GPU_LAYERS`,
`LLM_LOCAL_VERBOSE`.
Relative paths in `.env` are resolved against the **current working directory**
— run from the project root.

Tool gates: `ALLOWED_PLACES` (default `workspace=./workspace`) scopes the file
and command tools; `ENABLE_SHELL_TOOLS=0` (set `1` to allow approved shell
commands); `WEB_SEARCH_PROVIDER=bing` (only bing/no-key is implemented so far);
`AUTO_MEMORIZE=1` (auto-capture durable tool findings as low-importance global
facts; `0` disables — captured tools are only `read_file`/`ask_user`, never
web results); `MAX_AGENT_STEPS=8` (caps the agent tool-loop iterations).

Local model layout: the GGUF is a fixed name `model.gguf` inside a per-model
directory, resolved as `LLM_LOCAL_MODELS_DIR/LLM_LOCAL_MODEL_NAME/model.gguf`.
Current setup: `LLM_LOCAL_MODELS_DIR=./resources/models`,
`LLM_LOCAL_MODEL_NAME=qwen3-4b-instruct-gguf`.

The system prompt is no longer hardcoded. `SYSTEM_PROMPT_PATH` (default none)
points to a markdown file read at import time (`ENV.get_system_prompt(default)`),
e.g. `./resources/SYSTEM_PROMPT.md`. A fallback string is used when the variable
is unset. The `Chat` service uses it for every conversation.

Model defaults: Qwen3-4B, 4096 context, CPU only.
See `conventions.md` for the reminder that `ENV.init()` must precede any import
that resolves env-dependent values.

Runtime selection: `.env` holds default provider/model; `settings` table
(`services/settings.py`) holds the user's `/select model` override, applied to
the environment before the LLM provider is built (`utils/llm.py`).