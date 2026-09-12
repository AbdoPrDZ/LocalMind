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