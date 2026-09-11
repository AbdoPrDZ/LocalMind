# llm-ccp

Local, offline LLM platform: talk to a local model (Qwen3‑4B GGUF via
`llama-cpp-python`) through interchangeable interfaces.

One shared core, many front-ends. The `cmd` interface is done (`questionary`
CLI); `web`, `api`, and `desktop` are planned.

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
local model  (utils/llm.py)    llama-cpp singleton, Qwen3-4B GGUF
    │
tools        (tools/model.py)  generic CRUD tools generated from the model registry
    │
database     (database.py)     SQLAlchemy + SQLite
```

`projects` / `tasks` are **example scaffolding** — they prove the generic tool
generation, not the product goal. `Chat` / `Message` models are the product's
conversation store.

## Requirements

- Python 3.11+
- A local GGUF model (see below)

## Setup

```cmd
install.cmd
```

This creates `.venv`, then installs `requirements.txt`
(`llama-cpp-python`, `sqlalchemy`, `pydantic`, `python-dotenv`, `questionary`).

Place the model under:

```text
resources/models/qwen3-4b-instruct-gguf/model.gguf
```

Database and model paths are configured in `.env` at the project root:

```dotenv
DATABASE_URL=sqlite:///./resources/data/app.db
MODELS_DIR=./resources/models
MODEL_NAME=qwen3-4b-instruct-gguf
MODEL_CONTEXT_WINDOW=4096
MODEL_CPU_THREADS=8
MODEL_GPU_LAYERS=0
```

## Usage

Run from the project root:

```cmd
python main.py cmd                             & rem interactive questionary session
python main.py cmd "list projects"             & rem single-shot prompt
python main.py                                 & rem defaults to cmd
```

`web`, `api`, and `desktop` currently raise `NotImplementedError`.

## Project context for agents

`.agents/context/` holds AI-usable knowledge about this codebase. If you're an
agent, read `.agents/context/README.md` first, then the files relevant to your
task. See `AGENTS.md` for the full agent rules.