# LocalMind Context Engine v2 — Improvement Plan

Improvement plan distilled from `.agents/blueprints/CHATGPT_REVIEW.md` (and the
code-verification of that review). The central diagnosis: LocalMind does **not**
need more memory — it needs a better **context architecture**. Today the LLM is
asked to own its own memory (regenerate the entire conversation summary after
every answer), while the application compensates in Python
(`_extract_context`, `_merge_contexts`, `_limit_context`,
`_synthesize_tool_context`, `_capture_global_memories`).

The core philosophy change:

```text
BEFORE:  "The LLM remembers the conversation through a generated summary."
AFTER:   "LocalMind owns the memory; the LLM receives a dynamically
          assembled view of that memory."
```

The LLM should not be the database. The database should be the database. The
LLM is the reasoning layer that consumes a context package.

This blueprint is intentionally generic — implementable without hardcoding any
Project/Task/domain knowledge into the core.

---

## Current state (verified against the code)

- `Chat.send()` / `send_stream()` send exactly two messages:
  `[{"role": "system", "content": _system_prompt()}, {"role": "user", ...}]`
  (`apps/base.py:653-656`, `apps/base.py:692-695`). **Zero recent messages**.
- `_system_prompt()` (`apps/base.py:570-581`) = base prompt + a static
  global-memory snapshot (`build_global_context`, `services/global_context.py`)
  + `CONTEXT_INSTRUCTIONS` which force the model to wrap every reply in a
  `<context>...</context>` block restating the **entire conversation**
  (`apps/base.py:76-89`).
- Context is char-bounded, not token-bounded:
  `MAX_CONTEXT_CHARS = 12_000` with `context[-MAX_CONTEXT_CHARS:]`
  (`apps/base.py:50`, `apps/base.py:100-108`) — past the limit, the beginning
  is silently deleted.
- Retrieval is substring-only: `Memory.content.ilike(pattern)` /
  `Message.content.ilike(pattern)` (`services/memory.py:147-164`,
  `services/memory.py:195-221`).
- Global snapshot: `GLOBAL_CONTEXT_MAX_CHARS = 4000`,
  `GLOBAL_CONTEXT_MAX_ENTRIES = 12`, picks the top-12 **before** grouping, and
  a too-large type block `break`s the whole build so later types are never
  considered (`services/global_context.py:11-47`).
- Ranking uses only `importance.desc(), updated_at.desc()`
  (`services/memory.py:159`, `services/memory.py:174-176`).
- Auto-capture: `AUTO_CAPTURE_TOOLS = {"fetch_page", "web_search",
  "read_file", "ask_user"}` (`apps/base.py:68`) writes every fetched page /
  web search into global memory as a permanent fact (opt-out only via
  `AUTO_MEMORIZE=0`).
- `Memory` model has no confidence / status / lifecycle / conflict fields
  (`models/memory.py`).
- The agent tool-loop is a bare `while True:` with no step/repetition limits
  (`utils/agent.py:143`, `utils/agent.py:213`).
- `_build_agent()` loads every tool set every time: CRUD + memory + files +
  web + system + ask + shell (`apps/base.py:355-360`).
- Model window is 4096 (`LLM_LOCAL_CONTEXT_WINDOW` in `.env.example`,
  README.md:127) — every unused character of budget is wasted.
- Message rows already persist everything (`models/message.py`) — the data
  needed for recent-messages and history retrieval already exists.

---

## Target architecture

```text
                          USER
                           │
                           ▼
                     Chat Service          (public API unchanged: send / send_stream)
                           │
                           ▼
                   ┌───────────────┐
                   │ ContextEngine │
                   └───────┬───────┘
                           │
         ┌─────────────────┼──────────────────┐
         │                 │                  │
         ▼                 ▼                  ▼
    Chat State        Memory Store       Chat History
         │                 │                  │
         │                retrieval          │
         └──────────────┬─┴─────┬────────────┘
                        ▼
                 Reranker / Ranker
                        ▼
                  Token Budgeter
                        ▼
                   Prompt Builder
                        ▼
                    LLM / Agent
                        │
                 ┌──────┴──────┐
                 ▼             ▼
             Answer       State Extractor
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
              Chat State           Memory    → SQLite
```

Responsibility split (the point of the whole change):

```text
Message table    = source of truth (already persisted, never deleted)
Chat state       = small structured working state per conversation
Memory           = durable extracted knowledge (cross-chat)
Tool results     = transient working data (never durable by default)
```

---

## Phase 0 — Quick win: stop memory pollution

Do **before** any engine work; it is a small, isolated, high-value change.

- [ ] Shrink `AUTO_CAPTURE_TOOLS` (`apps/base.py:68`) to at most
      `{"ask_user", "read_file"}`. Remove `web_search` and `fetch_page`:
      retrieval results are not durable knowledge.
- [ ] Keep the `AUTO_MEMORIZE=0` opt-out; document the new default in
      `.env.example` and README.
- [ ] Add a regression test: a `web_search` / `fetch_page` result is never
      persisted to `memories` after a `send()`.

---

## Phase 1 — Context Engine (main milestone)

New `services/context/` package. The `Chat` service keeps its public API
(`send`, `send_stream`, `context`, `history`, `load`, `create`) and stops
orchestrating context mechanics itself.

```text
services/context/
├── __init__.py        # public: ContextEngine, ContextPackage
├── engine.py          # ContextEngine.build(chat_id, user_message, ...) -> ContextPackage
├── budget.py          # token budget from the model window, reserves output tokens
├── retrieval.py       # query-aware memory + history retrieval
├── ranking.py         # score/rank candidates (keyword × importance × confidence × recency)
├── formatter.py       # render ContextPackage into chat-completion messages
└── state.py           # structured per-chat state (read/merge/persist)
```

### 1.1 Recent conversation

Include the last N user/assistant turns from the `messages` table in the
prompt (moves the model off "summary-only" memory).

- Default ~6 turns; count is token-aware (fit recent turns until the
  `recent` budget is exhausted, oldest-out).
- Keep tool-message pseudo-turns out of the user-visible history.

### 1.2 Structured chat state

- Add an application-owned state to a chat. Storage: `state` JSON column on
  `chats` (nullable). Schema change with **no Alembic** — add an idempotent
  `_ensure_column()` helper in `database.py` (`PRAGMA table_info(chats)` →
  `ALTER TABLE chats ADD COLUMN state TEXT`).
- Initial state document (start small, grow later):
  ```json
  {
    "objective": "",
    "current_topic": "",
    "decisions": [],
    "constraints": [],
    "open_questions": [],
    "important_entities": [],
    "last_summary": ""
  }
  ```
- Population strategy during migration: keep the model-facing `<context>`
  block (the model is already a reliable summarizer for this small shape) but
  **change the instructions** (`apps/base.py:76-89`) from "restate the ENTIRE
  conversation in order" to "maintain a concise structured CHAT STATE —
  condense, supersede, drop the stale". The parser maps the submitted state
  onto the JSON document, capped by a much smaller budget (≈2k chars).
- The `Chat`/`ChatRecord.context` column and `get_chat_context` tool keep
  working; `state` augments them during migration (phase-out later).

### 1.3 Relevant global memory (query-aware)

Replace the static top-12 snapshot (`build_global_context`) with per-turn
retrieval using the incoming user message as the query:

- `retrieval.retrieve_memories(query, limit)` → scored candidates from
  `MemoryService`.
- `build_global_context` is either removed from the prompt path or shrunk to a
  tiny permanent baseline (identity + a few pinned entries). Per-call
  retrieval of 3–8 relevant memories replaces it.
- Keep `search_global_memory` as the explicit deep-retrieval tool the model
  can still call; automatic retrieval is the cheap common case.

### 1.4 Relevant old history

- Lightweight automatic recall for memory/decision questions: tokenize the
  user message, run `Message.content` scoring, surface 2–5 relevant old
  messages when the `retrieved` budget allows.
- No vector DB. Improved keyword + scoring only (see Phase 3).

### 1.5 Token budgeting

- Budget from the actual model window: `LLM_LOCAL_CONTEXT_WINDOW` (fallback
  4096 via `services/settings.py`).
- Reserve output (`max_tokens`); allocate the rest across
  system / memory / state / recent / retrieved / current-message.
- `budget.py` owns an estimator (chars→tokens with a mixed-language/unicode
  safety factor). Drop sections in priority order (lowest first), never by
  simply slicing `[-12000:]`:
  ```text
  P0 current user message
  P1 explicit constraints
  P2 active task state
  P3 recent conversation
  P4 relevant memories
  P5 retrieved history
  P6 low-confidence info
  ```
- Replace/retire `MAX_CONTEXT_CHARS`, `GLOBAL_CONTEXT_MAX_CHARS/ENTRIES`,
  `TOOL_CONTEXT_TAIL_BUDGET`, `TOOL_RESULT_TRIM_CHARS` semantics with token
  budget allocations.

### 1.6 Behavioral tests (acceptance criteria — write during Phase 1, not at the end)

`tests/test_context_engine.py` covering the review's five scenarios:

- [ ] T1: prefer React Native early → unrelated turns → "what mobile framework
  do I prefer?" returns React Native (memory retrieval).
- [ ] T2: "Use SQLite" → "switch to PostgreSQL" → "what database?" returns
  PostgreSQL, never "SQLite + PostgreSQL" (supersession).
- [ ] T3: decision in Chat A → asked in Chat B → found (cross-chat retrieval).
- [ ] T4: a web search about a price is asked in Chat B → "what do you
  remember about me?" is **not** polluted by the price (no auto-capture).
- [ ] T5: a conversation longer than the budget → important facts survive
  (context compression, not head-truncation).
- Plus: recent messages present in the assembled prompt; token budget never
  exceeded; `state` persisted/merged; `send`/`send_stream` surface unchanged.

---

## Phase 2 — Memory correctness

Move `Memory` from "a row" to "a managed knowledge item".

### 2.1 Model fields

Extend `models/memory.py` (same `_ensure_column()` treatment):

```text
confidence       float  default 0.5   — how sure we are
status           str    default "active" | "superseded" | "archived"
superseded_by    int    nullable      — id of the replacing memory
source_message_id int   nullable FK   — provenance
last_accessed_at datetime            — for recency ranking + staleness
access_count     int    default 0
```

### 2.2 Conflict / supersession

- In `MemoryService.create`, before insert: search same-type candidates
  (normalized + keyword overlap). If a new memory contradicts an **active**
  one (e.g. DB engine changed), mark the old `status="superseded"`,
  `superseded_by=new_id`, add the new one `active`.
- Duplicate guard (exact normalized match) stays as-is.
- Ranking becomes `relevance × importance × confidence × recency`, not
  `importance, updated_at` alone.

### 2.3 Explicit forget (privacy)

- New tool `forget_memory(memory_id | query)` → supersede or delete.
- New command `/memory forget N` in `apps/cmd/main.py`.
- The model can honor "forget that I prefer X."

### 2.4 Provenance

- Record `source_chat_id` (exists) and `source_message_id` on `create` from
  the message being answered.

---

## Phase 3 — Retrieval quality (no vector DB)

- SQLite **FTS5** virtual tables over `memories.content` and
  `messages.content` (shadow tables synced on write) for real keyword scoring
  — a big win over `LIKE '%q%'`.
- Hybrid ranker: FTS5 score + importance + confidence + recency, normalized.
- Embeddings/vector retrieval are a **separate later milestone** (local
  embeddings only, still SQLite-backed). Do not add Chroma/Qdrant/FAISS.
- Target test: memory "User prefers simple, minimalist interfaces" is found by
  "What UI style should I use?"

---

## Phase 4 — Agent efficiency

- Agent loop guardrails (`utils/agent.py`): `MAX_AGENT_STEPS` (default 8),
  max repeated identical tool calls (e.g. same `name+args` 3× → error),
  bounded tool-result serialization.
- Tool-result compression: per-tool compactors (web_search → query + top
  titles/URLs; fetch_page → title + first paragraphs + links; read_file →
  head + tail) instead of the blind first-600-chars slice.
- Structured tool errors: `{"ok": false, "error": {"code", "message",
  "retryable"}}` so small models can decide whether to retry.
- Dynamic/optional tool loading (expand file/shell/web only when relevant):
  evaluate token benefit; not a Phase-1 blocker. `ENABLE_SHELL_TOOLS` already
  gates the shell set.

---

## Phase 5 — Developer tooling

- `/context` (+`/context` in `apps/cmd/main.py`) debugger: per-section token
  table (system / memory / state / recent / retrieved / tools / user) vs.
  budget, plus retrieved memories with scores. Driven by
  `ContextEngine.debug()`.
- `/global` richer: `/global search Q`, `/global conflicts`, `/global stale`,
  `/global inspect N`.
- Usage/context-budget stats exposed via the existing `/usage` command.
- Run the full behavioral suite in normal `pytest` flow
  (`.venv\Scripts\python.exe -m pytest`).

---

## Non-goals (do NOT do in this plan)

- No vector database (Chroma/Qdrant/FAISS) — Phase 3 stays on SQLite.
- No graph database; `MemoryRelation` (supports/contradicts/supersedes) is a
  possible later extension, not part of this plan.
- No merging of `.agents/context/` (project knowledge) physically with the
  memory store; statistically retrieving from both in the engine is a future
  idea, not this milestone.
- No interface rewrites. `Chat` service surface and the three planned apps
  stay untouched.
- No full removal of the `<context>` mechanism yet — it is repurposed and
  capped in Phase 1, phased out only after `state` proves reliable.

---

## Execution order summary

```text
Phase 0  Quick win — stop web-memory pollution            (instant, isolated)
Phase 1  ContextEngine: recent msgs + chat state +        (main milestone)
         relevant memory + old-history + token budget
         + behavioral tests
Phase 2  Memory correctness: confidence, status,          (unblocks T2/T4)
         supersession, forget, provenance
Phase 3  Retrieval: FTS5 keyword → hybrid ranker          (unblocks memory QA)
Phase 4  Agent guardrails + tool-result compression
Phase 5  Developer tooling: /context & /global debuggers,
         budget stats
```

After each phase that changes models/services/api, sync
`.agents/context/` (`database.md`, `api.md`, `workflows.md`, `architecture.md`)
and update README.md / CHANGELOG.md / AGENTS.md / `.env.example` when
user-facing behavior or config changes.