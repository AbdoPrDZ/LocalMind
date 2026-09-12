# Database & Model Tooling

## Setup

- SQLAlchemy 2.x (`DeclarativeBase` from `utils/model.py`, subclasses are ORM models).
- SQLite, location from `DATABASE_URL` in `.env`
  (currently `resources/data/app.db`).
- `database.py` creates the engine at import time, so **`ENV.init()` must run
  before anything imports it** (see `conventions.md`).
- `init_db()` creates all tables for registered models.

## Model registry (`utils/registry.py`)

Models self-register with a decorator:

```python
@register_model()          # all ops enabled
@register_model(delete=False, update=False)  # selective
class Task(BaseModel): ...
```

- `MODELS` maps table name → model class; `get_model(name)` forwards unknown names.
- `crud_operations` on each class decides which CRUD tools get generated.

## Generic CRUD tools (`tools/model.py`)

No tool is hardcoded to a model. `build_crud_tools()` generates
`{operation}_{tablename}` tool instances for every registered model and every
enabled operation (`create`, `get`, `list`, `update`, `delete`).

- Pydantic input schemas are built at runtime from ORM columns
  (`build_schema`) — primary keys are exposed as `record_id`, never as editable columns.
- Results serialize datetimes to ISO strings (`_to_dict` + `_json_safe`).
- Operations run in their own session; failures roll back and return
  `{"success": False, "error": ...}`.

## Example models (`models/`)

`projects` and `tasks` are **examples** — they exist to validate the generic
tool generation, not as the product's business domain. When real domains appear,
extend the registry; the plumbing does not change.

`chats` and `messages` are the conversation store used by the `Chat` service in
`apps/base.py`. Both register read-only (`create/update/delete` disabled) so the
LLM can never tamper with its own history; only `Chat.send()` writes them. The
`chats.context` column holds the running conversation summary used instead of
the full history (see `api.md`).

`notes` (`models/note.py`) is a full-CRUD example of an LLM-facing store
(title, content, tags, created/updated by `utils.time.utcnow`); it is served by
the same generic tools as every registered model, proving that adding a product
feature means adding a model — not a tool.

## Global memory (`models/memory.py`)

`Memory` (table `memories`) persists cross-chat knowledge: `type`
(fact/preference/decision/topic), `content`, `importance` (1–4, default 2), an
optional `source_chat_id` FK to `chats.id` for provenance, and created/updated
timestamps via `utils.time.utcnow`.

- Deliberately **not** registered via `@register_model`, so no generic CRUD tools
  are generated. The LLM reaches memories only through the memory tools
  (`tools/memory.py`) → `MemoryService` (`services/memory.py`).
- No Alembic/migration system exists: `init_db()` runs
  `BaseModel.metadata.create_all(engine)`. Importing `models.memory` in
  `database.py` is what registers the table.

## Usage accounting (`models/usage.py`)

`Usage` (table `usage`) is a private accounting store: one row per chat session,
tracking token counts and estimated cost. Like `Memory` it is **not** registered
via `@register_model`, so the LLM can neither see nor touch it.

- Fields: `chat_id` FK → `chats.id` (indexed), `provider`, `model`,
  `started_at`, `ended_at` (nullable, set on exit), `prompt_tokens`,
  `completion_tokens`, `total_tokens`, `cost`.
- Written only through `UsageService` (`services/usage.py`): `start_session`
  (on `Chat.create()`/`Chat.load()`), `record` (after each `send()`), `close_session`
  (from `Chat.close()` on exit). Aggregations: `totals()`, `totals_for_chat()`,
  `totals_by_chat()`, `totals_by_model()`.
- Cost is an estimate: `estimate_cost()` uses `GEMINI_PRICING_PER_1M` (model
  suffixes `-preview`/`-latest` stripped via `model_base()`); any Gemini model
  is billed by name no matter which provider serves it (`gemini` or `openai`),
  everything else costs 0. The Gemini API exposes no exact billed amount or
  remaining quota. Importing `models.usage` in `database.py` registers the
  table.

## Runtime settings (`models/settings.py`)

`Setting` (table `settings`) is a simple key→value store, also **not**
registered via `@register_model`. It persists the user's runtime LLM
selection (keys `provider`, `<provider>_model`); the `.env` values
(`LLM_PROVIDER`, `LLM_GEMINI_MODEL`, `LLM_LOCAL_MODEL_NAME`) remain the
fallback defaults.

- Written only through `SettingsService` (`services/settings.py`): `get`/
  `set`, `set_provider`, `set_model`, `apply_to_env` (pushes overrides into
  `os.environ` before a provider is built). `get` tolerates a missing table
  (returns the default) so it is safe before `init_db()`.
- Resolution helpers: `resolve_provider()` (setting else `LLM_PROVIDER` or
  `"local"`) and `resolve_model(provider)` (setting else `LLM_GEMINI_MODEL`/
  `LLM_OPENAI_MODEL`/`LLM_LOCAL_MODEL_NAME`/`unknown`; env var per provider via
  `_model_env()`). Used by usage accounting, `get_llm()` and the cmd
  app's `/settings` display.
- `utils/llm.py::reset_llm()` drops the cached provider singleton so the next
  call rebuilds it with the new selection. Importing `models.settings` in
  `database.py` registers the table.