# AGENTS.md

## About the project

`LocalMind` is a local, offline LLM platform: you talk to a local model (Qwen3‑4B
GGUF via `llama-cpp-python`) through interchangeable interfaces. One shared
core serves many front-ends — the `cmd` interface is done (`questionary` CLI),
while `web`, `api`, and `desktop` are planned.

The core is generic: `apps/base.py` exposes a `Chat` service (replacing the old
`LLMBridge`) that any interface calls with `chat.send(prompt)`. Each `Chat` is a
persisted conversation (`Chat`/`Message` DB rows); `send()` saves the user
message, sends the current chat context (+ new message) to the LLM — full
history is NOT sent — and saves the reply. It boots the database and an `Agent`
that runs the chat/tool-loop against the model and dynamically generated CRUD
tools (handles both native and Qwen3 `<tool_call>` formats). A `<context>`
block in the reply is extracted and persisted as the running chat summary.

`projects`/`tasks` models are **example scaffolding** that proves the generic
tool generation — they are NOT the product goal. `Chat`/`Message` are the
product's conversation store.

## Project context

Before substantial work on this project, read the project context located in
`.agents/context/` — it is the reliable memory for understanding this codebase
without re-exploring it every session.

Start with `.agents/context/README.md`, then `config.md` and `structure.md`,
then the files relevant to your task (e.g. `database.md` for model changes,
`api.md` + `workflows.md` for interface work, `architecture.md` for system
overview). Use it to navigate, but always verify against the actual code.

## Rules for agents

Context & codebase:

- If context conflicts with actual code, trust the code and correct the context.
- Update `.agents/context/` automatically on significant changes (update mode
  is `automatic` per `config.md`); touch only the affected files.
- Never rewrite context wholesale for local changes.

Code changes:

- Follow `conventions.md` in `.agents/context/`: 2-space indentation, type
  hints, no speculative comments.
- Keep the system generic: tools are parameterized by model, never hardcoded;
  interfaces communicate with the LLM only through `apps/base.py`'s `Chat`
  service. Never write to `Chat`/`Message` rows directly.
- `ENV.init()` must run before any import that reads env values at import time
  (e.g. `database.py`) — keep the existing import ordering.
- Run everything from the project root; relative `.env` paths depend on cwd.
- Do not modify `projects`/`tasks` example scaffolding as if it were the product.

Commands:

- Run from the project root: `python main.py [app] [args...]`
  (e.g. `python main.py cmd "list projects"`, or `python main.py cmd` for the
  interactive questionary session).

## Quick facts

- Project: `LocalMind` — local, offline LLM platform with multiple interfaces
  (`cmd` done; `web`/`api`/`desktop` planned).
- One shared core, many front-ends: `apps/base.py` (`Chat` service) → `Agent` →
  local model (`llama-cpp-python`, Qwen3-4B GGUF) → generic CRUD tools
  (`tools/model.py`) and controlled global-memory tools (`tools/memory.py`;
  `MemoryService` in `services/memory.py`, `Memory` table in `models/memory.py`).
  A small bounded global-context snapshot is auto-injected into the prompt.
- `projects`/`tasks` models are example scaffolding, not the product goal.
  `Chat`/`Message` models are the conversation store.
- Run commands from the project root (`python main.py [app] [args...]`).
