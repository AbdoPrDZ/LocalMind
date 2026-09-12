# API & Interface Layer

The public contract every interface uses. Apps do NOT talk to the model
directly — they go through the `Chat` service.

## Chat service (`apps/base.py`)

Single shared gateway for all interfaces (cmd, web, api, desktop). A `Chat` is
a persisted conversation: `Chat`/`Message` rows in the SQLite DB.

- `Chat.create(title=None, system_prompt=None, max_tokens=1024)` — `ENV.init()`,
  `init_db()`, builds the `Agent` with CRUD tools, starts a new chat.
- `Chat.load(chat_id, ...)` — resume an existing chat; raises `ValueError`
  if the id is unknown.
- `chat.send(text) -> str` — the one call an interface needs. Saves the user
  message, sends `[system (+context), user]` (NOT the full history) to the LLM,
  saves the assistant reply, and returns it.
- `chat.send_stream(text) -> Generator[str, None, None]` — same as `send` but
  yields the reply in streaming chunks; the full reply is persisted once done.
  Interfaces that stream print each chunk as it arrives.
- `chat.context` — the running conversation summary (see below).
- `chat.history` — all previous `{"role", "content"}` messages (user/assistant).
- `.tools` / `.agent` — optional access to registered tools and the agent.
- `.id` / `.title` — the persisted chat's identity.

### Running chat context

The `Chat` service keeps a compact text **context** summary per chat (column
`chats.context`) instead of sending the full message history — a CPU-model
memory optimization. The context description (`CONTEXT_INSTRUCTIONS` in
`apps/base.py`, not a file) is appended to the system prompt with the current
summary, and the model is asked to append the COMPLETE updated summary as the
very last part of every answer wrapped in `<context>...</context>`.

- Non-streaming: `_extract_context()` splits the raw reply into the new context
  and the visible answer; the new context is saved via `_set_context()`.
- Streaming: `_stream_strip_context()` yields the visible text while buffering
  a small tail so tags split across chunks are never shown; the extracted
  context is saved when the stream completes. An unclosed `<context>` block is
  captured rather than leaked.
- `_limit_context()` clamps the persisted summary to `MAX_CONTEXT_CHARS`
  (12 000) so it can never grow unbounded.
- Failed/interrupted inference persists the user message but saves no assistant
  reply or context update.
- `_system_prompt()` injects a **bounded global context** snapshot (see below)
  between the base system prompt and the chat-context instructions, plus a
  `CURRENT CHAT ID:` line so the LLM can populate `source_chat_id` on
  `save_memory` calls.

The modules `Chat`/`Message` ORM classes register **read-only** for the LLM
(`create/update/delete` disabled) so only this service writes conversation data.

> Caveat: the local 4B model does not always emit the `<context>` tag on the
> first turn, so the produced context is best-effort.

## Agent (`utils/agent.py`)

Runs the tool-calling loop over a chat-completion message list (per the `Chat`
service this is the system prompt + current chat context + user message, not the
full history):

- `run(messages: list[dict]) -> str` — takes a chat-completion message list that
  already includes the system prompt and all prior turns; appends assistant
  choices and tool results as it iterates.
- `run_stream(messages: list[dict]) -> Generator[str, None, None]` — streaming
  variant that yields the plain-text answer in token chunks. Tool-calling rounds
  emit nothing (thinking + `<tool_call>` blocks are hidden); two output modes:
  Qwen3 template yields only text after the `response` marker; otherwise the
  `clean_answer()` result is emitted in chunks.
- Handles two tool-call formats: native `tool_calls` and Qwen3 GGUF
  `<tool_call>...</tool_call>` JSON blocks parsed via `parse_tool_calls()`.
- Executes tools, appends `{"role": "tool", "content": json.dumps(result)}`.
- `clean_answer()` removes the model's `thinking ... response` preamble and any
  stray `response` marker line, returning only the reply.
- Loop ends when the model replies with plain text.

## Tool (`utils/tool.py`)

ABC for all tools. Each tool declares:

- `name`, `description`, `input_model` (a Pydantic model).
- `schema()` — OpenAI-style function schema from `model_json_schema()`.
- `call(arguments)` — validates via the input model, then executes.
- `execute(arguments) -> Any` — implemented by subclasses.

## LLM singleton (`utils/llm.py`)

`get_llm()` lazily loads the `Llama` instance once. Settings come from `.env`
(`MODEL_CONTEXT_WINDOW`, `MODEL_CPU_THREADS`, `MODEL_GPU_LAYERS`,
`MODEL_VERBOSE`). Model location resolves via
`MODELS_DIR` + `MODEL_NAME` + `model.gguf` (see `architecture.md`).

## Global memory (`services/`, `tools/memory.py`)

Cross-chat persistence as a second layer under the existing chat context:

- `MemoryService` (`services/memory.py`) — `create` (with duplicate guard:
  normalized-content match before insert), `get/update/delete`, `search` (LIKE
  over content/type, ranked by importance), `list`, `get_chat_context(chat_id)`,
  `search_chat_history(query, chat_id?, limit)`. No vector DB, no business-domain
  coupling.
- Memory tools (`tools/memory.py`) — `search_global_memory`, `get_memory`,
  `get_chat_context`, `search_chat_history`, `save_memory`. Registered alongside
  CRUD tools in `_build_agent()`. The LLM never touches the DB directly
  (tool → `MemoryService` → SQLAlchemy only).
- `services/global_context.py::build_global_context()` — builds a small
  prompt-friendly snapshot (`GLOBAL_CONTEXT_MAX_CHARS` 4000,
  `GLOBAL_CONTEXT_MAX_ENTRIES` 12): top-N most important memories grouped by type
  (preferences/decisions/topics/facts), with an optional `CURRENT CHAT ID`
  header. The full memory table is never injected into a prompt.
- `models/memory.py` — the `Memory` ORM row; **not** in the CRUD registry
  (see `database.md`).

> Adding a new interface = build a thin front-end that calls
> `Chat.create()` then `chat.send()`. Never bypass the Chat service.