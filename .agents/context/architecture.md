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
services     (services/memory.py) MemoryService; global-context builder
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
2. `send()` saves the user message, builds `[system (+global context snapshot +
   context instructions + current chat summary), user]` and hands it to
   `Agent.run(messages)` — the full message history is NOT sent.
3. The agent sends the messages + tool schemas (CRUD, memory, **and** the
   scoped files/web/system/ask tools) to the model.
4. If the model asks for tools, the agent executes them and feeds results back.
   `ask_user` blocks on the interface's question handler; `run_command` blocks
   on the user's explicit approval before executing anything.
5. Repeats until the model produces a plain-text answer.
6. Context persistence: a `<context>...</context>` block in the reply is
   **merged** into the stored chat context (`_set_context` → `_merge_contexts`,
   which accumulates topics and never overwrites history); if the model sent no
   block, the agent's recorded tool results are folded in automatically
   (`_synthesize_tool_context`) and durable tools (`fetch_page`, `web_search`,
   `read_file`, `ask_user`) are captured as low-importance global facts
   (`_capture_global_memories`, gated by `AUTO_MEMORIZE=1` in `.env`). `send()`
   saves the cleaned assistant reply and returns it.
7. The agent accumulates provider-reported token usage (`take_usage()`); each
   `send()`/`send_stream()` records it into the open session. Cost is estimated
   per token (Gemini pricing in `services/usage.py`; local is tracked but free).

Global memory sits under the per-chat context: memories persist across chats
(`services/memory.py`, `models/memory.py`), and a small bounded snapshot of them
(`services/global_context.py`) is auto-injected into the prompt. The model can
retrieve more on demand via `search_global_memory` / `get_chat_context` /
`search_chat_history`, and persist durable knowledge via `save_memory`.

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
`LLM_FREE_BASE_URL`). Locally, the per-backend model vars are
`LLM_LOCAL_CONTEXT_WINDOW`, `LLM_LOCAL_CPU_THREADS`, `LLM_LOCAL_GPU_LAYERS`,
`LLM_LOCAL_VERBOSE`.
Relative paths in `.env` are resolved against the **current working directory**
— run from the project root.

Tool gates: `ALLOWED_PLACES` (default `workspace=./workspace`) scopes the file
and command tools; `ENABLE_SHELL_TOOLS=0` (set `1` to allow approved shell
commands); `WEB_SEARCH_PROVIDER=bing` (only bing/no-key is implemented so far);
`AUTO_MEMORIZE=1` (auto-capture durable tool findings as low-importance global
facts; `0` disables).

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