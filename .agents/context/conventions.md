# Conventions

Code style and structural rules to follow when touching `LocalMind`.

## Style

- **2-space indentation** (Python) throughout — do not introduce 4-space blocks.
- Type hints on every signature.
- No comments unless the author adds them; keep code self-explanatory.

## Structural rules

- All interfaces live under `apps/<name>/` and use `apps/base.py`'s `Chat`
  service to talk to the LLM. Never bypass the service or write to
  `Chat`/`Message` rows directly.
- Run from the **project root** (relative `.env` paths depend on cwd).
- `ENV.init()` must run **before** importing anything that reads env values at
  import time (e.g. `database.py`). Both `apps/base.py` and root `main.py`
  import `ENV` first, call `init()`, then import the rest — keep this ordering.
- Interfaces add the project root to `sys.path` at the top of their entry module.
- Standalone dev/ops scripts live under `scripts/` (e.g. `scripts/install_model.py`
  downloads GGUF models from Hugging Face into `resources/models/<name>/model.gguf`).
- Domain-independent services live under `services/`
  (e.g. `services/memory.py`, `services/global_context.py`). Keep them decoupled
  from the Project/Task demo scaffolding.
- Root `main.py` dispatches to interfaces via argparse positional `app`
  (`cmd` implemented; `web`/`api`/`desktop` raise `NotImplementedError`).
- Tools are always generic — parameterized by model, never hardcoded.

## Environment

- Config lives solely in `.env` (see `architecture.md`).
- Model + data assets live under `resources/` (`resources/models/…`,
  `resources/data/…`).
- The system prompt lives at `resources/SYSTEM_PROMPT.md`; path is wired via
  `SYSTEM_PROMPT_PATH` in `.env`.