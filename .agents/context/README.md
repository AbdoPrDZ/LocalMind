# Project Context

AI-readable knowledge layer for `LocalMind`, a local, LLM-powered project
management assistant.

## What this is

Compact, reliable knowledge that mirrors the real project so an AI agent can
understand and safely modify it without re-exploring everything each session.

## How to use it

Always read this README, `config.md` and `structure.md` first, then the files
relevant to your task. Use `structure.md` to pick files by task type and to
understand what belongs where.

## Files

- `config.md` — update mode, detail level, how context is maintained.
- `structure.md` — the file layout contract: what exists, what each file holds.
- `architecture.md` — layers and data flow (apps → Chat service → agent → LLM → tools).
- `database.md` — SQLAlchemy setup, model registry, CRUD tool generation.
- `api.md` — the interface layer: `Chat`, `Agent`, `Tool` contracts.
- `workflows.md` — launch flows and the chat/tool calling pipeline.
- `conventions.md` — code style and structural conventions.
- `domains/` — business domain knowledge.
  - `domains/README.md` — navigation + rules for adding real domains (none yet;
    `projects`/`tasks` models are example scaffolding, not a domain).

## Navigation by task type

- **Launching / adding an interface** → `workflows.md`, `api.md`
- **Models / database changes** → `database.md`
- **Tool behavior / agent loop** → `api.md`, `workflows.md`, `architecture.md`
- **Model / env configuration** → `conventions.md`, `architecture.md`
- **New business domain** → `structure.md`, `domains/README.md`

## Sources of truth

Context is derived knowledge. If it conflicts with the actual project, trust
the project and correct the context. Full rules: `config.md` + `structure.md`.