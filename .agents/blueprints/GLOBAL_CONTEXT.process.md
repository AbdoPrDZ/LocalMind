# GLOBAL_CONTEXT — Execution Process

Process checklist for implementing **Global Memory for the Generic Local Agent**
(blueprint: `.agents/blueprints/GLOBAL_CONTEXT.md`). Each task is a checkbox;
tick it off as the task is finished. Keep the framework generic — never
hardcode Project/Task/EMoveX/Odoo knowledge into the core.

Project-specific notes (verified against the codebase):

- There is **no** `ToolRegistry` class and **no** `tools` kwarg on the agent
  build path today. `Agent` receives `tools: list[Tool]` and
  `apps/base.py::_build_agent()` calls `build_crud_tools()`. Memory tools get
  registered by returning them from the same agent/tool path.
- There is **no** Alembic/migration system. `database.py::init_db()` runs
  `BaseModel.metadata.create_all(engine)`, so a new table is picked up
  automatically once the model module is imported. Do not add a migration
  system for this.
- The `Memory` model must **not** be registered via `@register_model(...)`.
  Generic CRUD tools are generated from that registry, and the LLM must reach
  memories only through the controlled memory tools → `MemoryService` (tool
  security, blueprint §10).

## Phase 0 — Design & inspection

- [x] Review blueprint `.agents/blueprints/GLOBAL_CONTEXT.md` end to end.
- [x] Map existing architecture: `apps/base.py` (Chat), `utils/agent.py`
      (Agent), `utils/tool.py` (Tool ABC), `tools/model.py` (CRUD tools),
      `database.py`, `models/`, `utils/registry.py`, streaming path.

## Phase 1 — Model

- [x] Add `models/memory.py`: `Memory` ORM class on `BaseModel`
      (id, type, content, importance, source_chat_id, created_at, updated_at).
- [x] Fields: `type` (fact | preference | decision | topic),
      `importance` (int 1–4, default 2), `source_chat_id` (nullable FK to
      `chats.id`), timestamps via `utils/time.utcnow`.
- [x] Add indexes on type, importance, source_chat_id, created_at/updated_at.
- [x] Export `Memory` from `models/__init__.py`.
- [x] Import the module in `database.py::init_db()` so `create_all` creates
      the table (no migration file needed).

## Phase 2 — MemoryService

- [x] Create service layer (`services/` package or equivalent per convention)
      with `MemoryService`.
- [x] `create(**fields)` — with duplicate guard and provenance
      (`source_chat_id`).
- [x] `get(memory_id)` / `update(memory_id, **fields)` / `delete(memory_id)`.
- [x] `search(query, limit)` — simple SQL `LIKE` search over content/type,
      ranked (no vector DB, blueprint §17).
- [x] `list(...)` — filter/paginate (type, importance, limit).
- [x] Duplicate detection: normalized-content match (lowercase, collapsed
      whitespace) before insert (blueprint §8).
- [x] `get_chat_context(chat_id)` — return the stored chat `context` without
      loading full message history.
- [x] `search_chat_history(query, chat_id?, limit)` — simple DB search over
      messages, bounded results.
- [x] Service stays independent of Project/Task (blueprint §2, §11).

## Phase 3 — Global context builder

- [x] Implement bounded global-context snapshot (blueprint §4): pick only
      important/relevant memories, group by type (preferences/decisions/
      topics), cap by char/token limit.
- [x] Encapsulate formatting in a dedicated component/service, not hardcoded
      in `Chat`.

## Phase 4 — Memory tools

- [x] Create `tools/memory.py` with `Tool` subclasses + `build_memory_tools()`.
- [x] `search_global_memory(query)` → relevant entries (id, type, content,
      importance, source chat).
- [x] `get_memory(memory_id)` → one entry.
- [x] `get_chat_context(chat_id)` → stored chat context.
- [x] `search_chat_history(query, chat_id?, limit)` → message excerpts.
- [x] `save_memory(type, content, importance?, source_chat_id?)` — description
      must discourage trivial/duplicate/redundant saves (blueprint §3).
- [x] Tool descriptions steer the LLM to search before guessing (blueprint §6).
- [x] Keep `Tool.call()` (pydantic validation) as the only entry — LLM never
      touches DB/Service directly (blueprint §10).

## Phase 5 — Chat & Agent integration

- [x] Register memory tools in `apps/base.py::_build_agent()` alongside
      `build_crud_tools()` (blueprint §13).
- [x] `Chat._system_prompt()`/message build injects SYSTEM PROMPT + GLOBAL
      CONTEXT (bounded snapshot) + CURRENT CHAT CONTEXT + USER MESSAGE
      (blueprint §5, §14).
- [x] Keep per-chat context behavior (`<context>` extraction) unchanged.
- [x] Do not inject full global memory / all chats into the prompt.
- [x] Public API stays `chat.send(...)`, `send_stream(...)`, `context`,
      `history` (blueprint §14).
- [x] Save-memory capability wired so a meaningful interaction can call
      `save_memory` (tool path, blueprint §7 — no separate extraction
      pipeline yet).
- [x] Ensure `send_stream` preserves streaming and never leaks tool/context/
      memory metadata into the visible answer (blueprint §15).

## Phase 6 — Tests

- [x] Memory persistence: create → retrieve → update → delete.
- [x] Search: several memories, topic search returns relevant ones.
- [x] Provenance: memory → `source_chat_id`.
- [x] `get_chat_context(chat_id)` retrieval.
- [x] Global context: important entries appear, output is bounded, unrelated
      memories not dumped.
- [x] Tool calls: memory tools validate arguments and return controlled
      results.

## Phase 7 — Verification & docs

- [x] Verify Project/Task demo still works (CRUD tools unaffected).
- [x] Verify normal chats still work and per-chat context persists.
- [ ] Verify streaming still works (no stray tags).
- [ ] Confirm real LLM call (`python main.py cmd ...`) runs without errors.
- [x] Sync `.agents/context/` (database.md, api.md, workflows.md,
      architecture.md, structure.md, domains if needed).
- [x] Update AGENTS.md / README.md / CHANGELOG.md if user-facing behavior
      changed.