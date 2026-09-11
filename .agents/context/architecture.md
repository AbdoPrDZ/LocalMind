# Architecture

## What this project is

`llm-ccp` is a **platform for talking to a local LLM through multiple
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
local model  (utils/llm.py)    llama-cpp singleton, Qwen3-4B GGUF
    │
tools        (tools/model.py)  generic CRUD tools generated from registry
    │
database     (database.py)     SQLAlchemy + SQLite
```

## Data flow (chat)

1. An interface calls `Chat.create()` (or `Chat.load(id)`), then `chat.send(text)`.
2. `send()` saves the user message, builds `[system (+context instructions and
   current chat summary), user]` and hands it to `Agent.run(messages)` — the
   full message history is NOT sent.
3. The agent sends the messages + tool schemas to the model.
4. If the model asks for tools, the agent executes them and feeds results back.
5. Repeats until the model produces a plain-text answer.
6. Any `<context>...</context>` block in the reply is saved as the new chat
   context and stripped; `send()` saves the cleaned assistant reply and
   returns it.

## Configuration

All runtime config lives in `.env` at the project root (loaded by
`utils/env.py`). Required vars: `DATABASE_URL`, `MODELS_DIR`, `MODEL_NAME`.
Relative paths in `.env` are resolved against the **current working directory**
— run from the project root.

Model layout: the GGUF is a fixed name `model.gguf` inside a per-model
directory, resolved as `MODELS_DIR/MODEL_NAME/model.gguf`. Current setup:
`MODELS_DIR=./resources/models`, `MODEL_NAME=qwen3-4b-instruct-gguf`.

The system prompt is no longer hardcoded. `SYSTEM_PROMPT_PATH` (default none)
points to a markdown file read at import time (`ENV.get_system_prompt(default)`),
e.g. `./resources/SYSTEM_PROMPT.md`. A fallback string is used when the variable
is unset. The `Chat` service uses it for every conversation.

Model defaults: Qwen3-4B, 4096 context, CPU only.
See `conventions.md` for the reminder that `ENV.init()` must precede any import
that resolves env-dependent values.