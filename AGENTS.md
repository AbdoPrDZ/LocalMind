# AGENTS.md

## About the project

`LocalMind` is an LLM platform: you talk to a local model (Qwen3‑4B GGUF via
`llama-cpp-python`, offline) or an online model (Gemini via `google-genai`)
through interchangeable interfaces, selected by `LLM_PROVIDER` in `.env`. One
shared core serves many front-ends — the `cmd` interface is done
(`questionary` CLI), while `web`, `api`, and `desktop` are planned.

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

- **Always use the project virtualenv interpreter** — never bare `python` or
  `py` (bare `python` hits the Microsoft Store stub; `py` selects a bare
  interpreter without the project deps). Windows:
  `.venv\Scripts\python.exe`. Unix/macOS: `.venv/bin/python`.
- Run from the project root: `.venv\Scripts\python.exe main.py [app] [args...]`
  (e.g. `.venv\Scripts\python.exe main.py cmd "list projects"`, or
  `.venv\Scripts\python.exe main.py cmd` for the interactive questionary
  session). For tests: `.venv\Scripts\python.exe -m pytest`.
- Verify without running the app:
  `.venv\Scripts\python.exe -c "..."`.

## Quick facts

- Project: `LocalMind` — LLM platform with interchangeable interfaces
  (`cmd` done; `web`/`api`/`desktop` planned), running on a local model
  (`llama-cpp-python`, Qwen3 GGUF) or online (Gemini, `google-genai`).
- One shared core, many front-ends: `apps/base.py` (`Chat` service) → `Agent` →
  LLM provider (`utils/llm.py` factory over `utils/providers/`; `LLM_PROVIDER`
  in `.env` selects `local` or `gemini`) → generic CRUD tools
  (`tools/model.py`) and controlled global-memory tools (`tools/memory.py`;
  `MemoryService` in `services/memory.py`, `Memory` table in `models/memory.py`).
  A small bounded global-context snapshot is auto-injected into the prompt.
- `projects`/`tasks` models are example scaffolding, not the product goal.
  `Chat`/`Message` models are the conversation store.
- Run commands from the project root with the venv interpreter
  (`.venv\Scripts\python.exe main.py [app] [args...]`).
