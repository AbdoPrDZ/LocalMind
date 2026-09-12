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
    │
services     (services/memory.py) MemoryService; global-context builder
             (services/usage.py)  UsageService; token/cost accounting per session
             (services/settings.py) SettingsService; runtime provider/model
                                   overrides (settings table, `.env` is default)
    │
LLM backend  (utils/llm.py)    provider factory (get_llm) over
             (utils/providers/)  local llama-cpp OR online Gemini OR an
                                 OpenAI-compatible backend that serves both
                                 free routers and Gemini via one interface
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
3. The agent sends the messages + tool schemas (CRUD **and** memory tools) to the model.
4. If the model asks for tools, the agent executes them and feeds results back.
5. Repeats until the model produces a plain-text answer.
6. Any `<context>...</context>` block in the reply is saved as the new chat
   context and stripped; `send()` saves the cleaned assistant reply and returns it.
7. The agent accumulates provider-reported token usage (`take_usage()`); each
   `send()`/`send_stream()` records it into the open session. Cost is estimated
   per token (Gemini pricing in `services/usage.py`; local is tracked but free).

Global memory sits under the per-chat context: memories persist across chats
(`services/memory.py`, `models/memory.py`), and a small bounded snapshot of them
(`services/global_context.py`) is auto-injected into the prompt. The model can
retrieve more on demand via `search_global_memory` / `get_chat_context` /
`search_chat_history`, and persist durable knowledge via `save_memory`.

## Configuration

All runtime config lives in `.env` at the project root (loaded by
`utils/env.py`). `DATABASE_URL` is always required. `LLM_PROVIDER` picks the
backend: `local` (default) additionally requires `MODELS_DIR`, `MODEL_NAME`;
`gemini` uses `GEMINI_API_KEY` and optional `GEMINI_MODEL` (online); `openai`
serves free routers AND Gemini through one OpenAI-compatible interface — model
ids starting with `gemini-` go to Gemini (`GEMINI_API_KEY`,
`GEMINI_OPENAI_BASE_URL`), anything else routes to the free backend
(`OPENAI_API_KEY`/`OPENROUTER_API_KEY`, `OPENAI_BASE_URL` default
`https://openrouter.ai/api/v1`, `OPENAI_MODEL` default `openrouter/free`;
~140 free model ids in `resources/models/free_models.json`).
Relative paths in `.env` are resolved against the **current working directory**
— run from the project root.

Local model layout: the GGUF is a fixed name `model.gguf` inside a per-model
directory, resolved as `MODELS_DIR/MODEL_NAME/model.gguf`. Current setup:
`MODELS_DIR=./resources/models`, `MODEL_NAME=qwen3-4b-instruct-gguf`.

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