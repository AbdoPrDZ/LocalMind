# LocalMind

LLM platform: talk to a local model (Qwen3 GGUF via `llama-cpp-python`) or an
online model (Gemini) through interchangeable interfaces.

One shared core, many front-ends. The `cmd` interface is done (`questionary`
CLI); `web`, `api`, and `desktop` are planned.

- [Architecture](#architecture)
- [Requirements](#requirements)
- [Setup](#setup)
- [LLM backends](#llm-backends)
- [Usage](#usage)
  - [`python main.py cmd` arguments](#python-mainpy-cmd-arguments)
  - [Interactive slash commands](#interactive-slash-commands)
  - [Runtime model selection](#runtime-model-selection)
- [Scripts](#scripts)
  - [`scripts/install_model.py`](#scriptsinstall_modelpy)
  - [`scripts/usage_report.py`](#scriptsusage_reportpy)
- [Project context for agents](#project-context-for-agents)

## Architecture

```text
interfaces/  (apps/)       cmd (done) | web, api, desktop (planned)
    │  Chat.create() / Chat.load() / chat.send()
shared service (apps/base.py)   Chat: ENV init, DB init, agent build,
                                persists Chat + Message, sends chat context
                                (not full history) to the LLM
    │
agent        (utils/agent.py)  tool-calling loop, handles native + Qwen3 <tool_call>
    │
LLM backend  (utils/llm.py)    provider factory (get_llm) over
             (utils/providers/)  local llama-cpp, Gemini, OpenAI-compatible
                                  routers, keyless free endpoints — same API
    │
tools        (tools/*.py)         generic CRUD tools from the model registry,
                                  plus scoped files/web/system/shell/ask tools
    │
database     (database.py)     SQLAlchemy + SQLite
```

`projects` / `tasks` are **example scaffolding** — they prove the generic tool
generation, not the product goal. `Chat` / `Message` models are the product's
conversation store.

The agent also carries **global memory** across conversations: memories
(facts, preferences, decisions, topics) persist in the `Memory` table, a small
bounded snapshot is injected into every prompt, and the model retrieves/extends
it through `search_global_memory`, `get_memory`, `get_chat_context`,
`search_chat_history`, and `save_memory`.

Beyond CRUD and memory, the agent gets a safe, toolset that lets it actually
do things — each gated so the model cannot misbehave on its own:

- **Files** (`tools/files.py`): `read_file`, `write_file`, `list_dir` scoped
  to the folders in `ALLOWED_PLACES` (default `workspace=./workspace`). Every
  call names a `place` and is containment-checked, so the model can never
  escape the configured folders.
- **Web** (`tools/web.py`): keyless `web_search` (Bing RSS) and `fetch_page`
  (readable text) — no API key required.
- **System** (`tools/system.py`): `current_datetime` (IANA/local), machine info,
  and Windows clipboard read/write.
- **Shell** (`tools/shell.py`): `run_command` runs a command **only after you
  approve it**. It is off unless `ENABLE_SHELL_TOOLS=1` in `.env`; the model
  must describe the command, you confirm or cancel in the terminal, and the
  command's working directory stays inside the allowed folders.
- **Ask** (`tools/ask.py`): `ask_user` lets the model ask you a clarifying
  question mid-conversation (choices or free text) and wait for your answer.
- **Notes** (`models/note.py`): a registered store (title/content/tags) served
  by the same generic CRUD tools as every other model.

Every tool result is **remembered for you**: the chat context accumulates the
whole conversation (topics are appended, never overwritten), and tool findings —
fetched pages, web searches, read files, your answers — are auto-captured as
low-importance global facts (`AUTO_MEMORIZE=1` in `.env`, set `0` to disable),
so durable knowledge (your GitHub profile, a URL, a file you asked it to read)
survives across chats even when the model never echoes it back.

```dotenv
ALLOWED_PLACES=workspace=./workspace   # optional: docs=./docs, scratch=./tmp, ...
ENABLE_SHELL_TOOLS=0                   # set to 1 to allow (approved) shell commands
AUTO_MEMORIZE=1                        # auto-capture tool findings as global facts
```

Usage (tokens + estimated cost) is tracked per chat session in the `usage`
table: a session opens when a chat starts/resumes, accumulates tokens on every
`send()`, and closes when the chat exits. Cost is an estimate only (Gemini is
billed per token; local runs are tracked but free). The cmd app prints a
summary on exit; full per-chat/per-model reports are available with:

```cmd
python scripts/usage_report.py
```

## Requirements

- Python 3.11+
- A local GGUF model (offline mode) **or** a Gemini API key (online mode)

## Setup

```cmd
install.cmd
```

This creates `.venv`, then installs `requirements.txt`
(`llama-cpp-python`, `google-genai`, `sqlalchemy`, `pydantic`,
`python-dotenv`, `questionary`).

Download the model (only needed for offline mode):

```cmd
python scripts/install_model.py qwen3-4b-instruct-gguf
python scripts/install_model.py --list        & rem list the registry
python scripts/install_model.py --all         & rem install everything
```

This saves the GGUF as `resources/models/qwen3-4b-instruct-gguf/model.gguf`.

Database and model paths are configured in `.env` at the project root:

```dotenv
DATABASE_URL=sqlite:///./resources/data/app.db
LLM_LOCAL_MODELS_DIR=./resources/models
LLM_LOCAL_MODEL_NAME=qwen3-4b-instruct-gguf
LLM_LOCAL_CONTEXT_WINDOW=4096
LLM_LOCAL_CPU_THREADS=8
LLM_LOCAL_GPU_LAYERS=0
```

## LLM backends

The backend is switched with `LLM_PROVIDER` in `.env`:

- **`local`** (default) — runs the GGUF model offline via `llama-cpp-python`
  (`LLM_LOCAL_MODELS_DIR`/`LLM_LOCAL_MODEL_NAME` must point at a downloaded model).
- **`gemini`** — talks to Google's Gemini API over the network
  (`google-genai`). Only `DATABASE_URL` plus the Gemini settings are used;
  `LLM_LOCAL_MODELS_DIR`/`LLM_LOCAL_MODEL_NAME` are not required.
- **`openai`** — one OpenAI-compatible backend for free routers *and* Gemini.
  Models starting with `gemini-` use Gemini's OpenAI-compatible endpoint
  (`LLM_GEMINI_API_KEY`, `LLM_GEMINI_OPENAI_BASE_URL`); every other model
  routes to the free backend (`LLM_OPENAI_API_KEY`/`LLM_OPENROUTER_API_KEY`,
  `LLM_OPENAI_BASE_URL`, default `https://openrouter.ai/api/v1`). See
  `resources/models/free_models.json` for ~140 free model ids.
- **`free`** — a hosted free LLM with **no API key at all**. The endpoint and
  model come from `resources/models/keyless_models.json` (default: Pollinations
  anonymous tier, `openai-fast`). `LLM_FREE_ENDPOINT`/`LLM_FREE_MODEL` select
  them; `LLM_FREE_BASE_URL` overrides the endpoint. No fallback and no
  switching — you pick it, LocalMind talks to exactly that endpoint.
  Experimental: keyless tiers can be rate-limited or answer with injected
  promo/budget notices. A built-in notice-guard detects those ads, retries once
  (nudging the model not to advertise), and raises a clear error instead of
  showing you the ad — a reply is never surfaced polluted.

```dotenv
LLM_PROVIDER=gemini
LLM_GEMINI_API_KEY=your-api-key          # https://aistudio.google.com/apikey
LLM_GEMINI_MODEL=gemini-3.5-flash        # default if omitted
```

```dotenv
LLM_PROVIDER=openai
LLM_OPENAI_API_KEY=your-key              # or LLM_OPENROUTER_API_KEY
LLM_OPENAI_MODEL=openrouter/free         # default if omitted; see resources/models/free_models.json
LLM_OPENAI_BASE_URL=https://openrouter.ai/api/v1
LLM_GEMINI_API_KEY=your-key              # only if you use gemini-* models through this provider
```

```dotenv
LLM_PROVIDER=free
LLM_FREE_ENDPOINT=pollinations           # keyless endpoint in resources/models/keyless_models.json
LLM_FREE_MODEL=openai-fast               # model at that endpoint (default if omitted)
# LLM_FREE_BASE_URL=https://...         # optional override; no API key is ever required
```

Providers live in `utils/providers/` and all speak the same OpenAI-style
chat/completion API, so the tool-calling loop in `utils/agent.py` works
unchanged with either backend.

## Usage

Run from the project root:

```cmd
python main.py cmd                             & rem interactive questionary session
python main.py cmd "list projects"             & rem single-shot prompt
python main.py                                 & rem defaults to cmd
```

`web`, `api`, and `desktop` currently raise `NotImplementedError`.

### `python main.py cmd` arguments

```cmd
python main.py cmd [prompt] [--chat CHAT_ID]
```

| Argument      | Description                                                        |
| ------------- | ------------------------------------------------------------------ |
| `prompt`      | Ask a single question and exit. Omit it to start the interactive `questionary` loop. |
| `--chat <id>` | Resume an existing chat by id instead of starting a new one (verbalized as "Chat #<id> …"). |

### Interactive slash commands

A chat is only created for a **real message** — starting the app (or running
all-command sessions) creates no chat row, and a line starting with `/` is
handled locally instead of being sent to the assistant. On the first real
message the chat is created and the model generates a short **title** for it
(falling back to the first user message if the call fails). Unknown `/`
commands are rejected locally, never saved as messages.

| Command          | Action                                                          |
| ---------------- | --------------------------------------------------------------- |
| `/h`, `/help`    | Show the command help.                                          |
| `/q`, `/quit`    | End the session.                                                |
| `/usage`         | Show token/cost usage for this chat and in total (by model).    |
| `/context`       | Show the running chat context summary.                          |
| `/g`, `/global`  | Show the global memory context (cross-chat knowledge).          |
| `/chats`         | List all chats (id, created, message count, context preview).   |
| `/select chat <id>` | Switch to (resume) another chat.                             |
| `/select model <provider> <name>` | Switch LLM provider/model (persisted).   |
| `/settings`      | Show the active provider/model and whether it is overridden.    |
| `/tools`         | List the tools the assistant can call.                          |

Anything else is sent to the assistant as a prompt.

### Runtime model selection

`/select model <provider> <name>` switches the LLM at runtime and **persists**
the choice in the `settings` table — the `.env` values stay as defaults:

```cmd
/select model gemini gemini-3.5-flash-lite
/select model local qwen3-4b-instruct-gguf
/select model openai openrouter/free
```

The provider must be one of `PROVIDERS` (`local`, `gemini`, `openai`); a gemini
selection requires `LLM_GEMINI_API_KEY` in `.env`, and a local selection requires
the model to be installed (`scripts/install_model.py <name>`). The `openai`
provider serves both free routers and Gemini through one OpenAI-compatible
interface: any model starting with `gemini-` goes to Gemini, anything else
routes to the free backend (`LLM_OPENAI_BASE_URL`, default OpenRouter). See
`resources/models/free_models.json` for ~140 free model ids and the router API
key/base url each needs. The active provider/model is
written into the environment before the LLM provider is (re)built, and usage
accounting records the newly selected provider/model for new sessions.

On exit (interactive or single-shot) the app prints a usage summary for the
session (tokens + estimated cost, plus global totals by model) and closes the
usage session.

## Scripts

Extra command-line tooling lives in `scripts/`.

### `scripts/install_model.py`

Downloads GGUF models into `resources/models/<name>/model.gguf`:

```cmd
python scripts/install_model.py <name> [<name> ...]   & rem install one or more models
python scripts/install_model.py --all                 & rem install every model in the registry
python scripts/install_model.py --list                & rem list the registry and exit
```

| Argument | Description                                             |
| -------- | ------------------------------------------------------- |
| `names`  | Model name(s) from the registry (see `--list`) to install. |
| `--all`  | Install every model in the registry.                    |
| `--list` | Print name / size / description for each registry model, then exit. |

Requires `huggingface-hub`; downloads from Hugging Face in the background.

### `scripts/usage_report.py`

Prints the usage accounting table (`usage` DB table):

```cmd
python scripts/usage_report.py             & rem global + per-chat + per-model totals
python scripts/usage_report.py --chat 3    & rem totals for chat #3 only
```

| Argument     | Description                                                    |
| ------------ | -------------------------------------------------------------- |
| `--chat <id>`| Limit the report to a single chat id (its sessions, not just its aggregate). |

## Project context for agents

`.agents/context/` holds AI-usable knowledge about this codebase. If you're an
agent, read `.agents/context/README.md` first, then the files relevant to your
task. See `AGENTS.md` for the full agent rules.