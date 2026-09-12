# Workflows

## Launch

```bash
python main.py cmd                      # interactive questionary session
python main.py cmd "list projects"      # single-shot; prompt forwarded to cmd app
python main.py web | api | desktop      # NotImplementedError (planned)
python main.py                          # defaults to cmd
```

Root `main.py` parses `app` + forwards extra args, then hands off to
`apps/cmd/main.py`. Extra args are re-injected into `sys.argv` so the cmd app's
own argparse sees just its arguments.

## Chat pipeline

1. Interface creates/loads a chat via `Chat.create()` / `Chat.load(id)`.
2. `chat.send(text)` saves the user message, builds
   `[system (+global snapshot +context), user]`.
3. `Agent.run(messages)` sends the system prompt + global-context snapshot +
   current chat context + the new user message with tool schemas to the model
   (NOT the full history).
4. Model may reply with tool calls (native or Qwen3 `<tool_call>` XML blocks) —
   CRUD tools (`create_*`, `list_*`, …) and memory tools
   (`search_global_memory`, `save_memory`, `get_chat_context`,
   `search_chat_history`, `get_memory`), plus the scoped tools:
   files (`read_file`/`write_file`/`list_dir`, `place`-scoped),
   web (`web_search`/`fetch_page`, keyless), system (`current_datetime`,
   `get_system_info`, clipboard), `ask_user` and `run_command`.
5. Agent executes each call, appends a `tool` role message with JSON results.
   `ask_user` and `run_command` **block**: they hand a question to the app's
   question handler and wait — the user's answer (or approval/cancel) decides
   the tool result. A missing handler or dismissed prompt returns an error to
   the model, never a crash.
6. Loop until the model returns a plain-text answer; `clean_answer()` strips the
   `thinking` preamble (and `<thinking>...</thinking>` / template leaks).
7. Context persistence: if the reply contains a `<context>...</context>` block,
   `_extract_context()` extracts it and `_set_context()` merges it into the
   stored chat context (`_merge_contexts` — accumulates, never replaces). When
   the model sends no block, the agent's recorded `tool_results` are folded in
   instead (`_synthesize_tool_context`), and durable findings (`fetch_page`,
   `web_search`, `read_file`, `ask_user`) are auto-captured as low-importance
   global facts (`_capture_global_memories`). The cleaned assistant reply is
   then saved and returned.

Memory workflow: the global-context snapshot primes the model; when it needs
more it calls `search_global_memory` → optionally `get_chat_context(source
chat)` or `search_chat_history`; durable findings are stored via `save_memory`
(duplicate-guarded, provenance `source_chat_id`) — and, since this release,
tool results are also captured automatically so nothing the model fetched once
is lost if it forgets to save it.

Streaming variant: `chat.send_stream(text)` calls `Agent.run_stream()`; the
answer is yielded token-by-token (only text after the `response` marker for
Qwen3, whole answer chunked otherwise). `_stream_strip_context()` strips any
`<context>...</context>` block from the stream and the full reply + context are
saved on completion.

Usage workflow: `Chat.create()`/`Chat.load()` open a `Usage` session; after each
`send()`, `Agent.take_usage()` returns the tokens the provider reported and
`UsageService.record()` accumulates them (plus estimated cost) into the session.
On exit the interface calls `chat.close()` — the cmd app prints a per-session
plus global summary first, then closes. Full history is available via
`scripts/usage_report.py` (global, per-chat, per-model).

## CMD interface details (`apps/cmd/main.py`)

- No prompt argument → `questionary.text` interactive loop; `exit`/`quit`
  (or Ctrl+C) ends it. **Lazy chat creation**: no chat row exists until the
  first real (non-command) message, which calls `Chat.create()` and announces
  the tools/model. Single-shot `python main.py cmd "p"` creates one for the
  prompt. `--chat <id>` resumes the given chat instead. Because chats are
  created lazily, `main()` calls `init_db()` itself at startup so
  command-only sessions (`/chats`, `/usage`) work on a fresh database.
- Lines starting with `/` are handled locally by `SLASH_COMMANDS` and are
  NEVER persisted as messages — an unknown `/...` command prints an error and
  is not forwarded to the assistant. Commands that need a chat (`/usage`,
  `/context`, `/global`, `/tools`) print a hint until the first message
  exists; `/chats`, `/settings`, `/select ...`, `/h`, `/q` work chat-less.
- The question handler is `_ask_value` (questionary `select` when the tool
  passes `options`, else a free-text prompt; aborted prompts return `None`).
  Every `Chat.create()`/`Chat.load()` call passes it, so `ask_user` and the
  `run_command` approval dialogs work in interactive and single-shot mode.
- Slash commands (interactive): `/h`/`/help` help, `/q`/`/quit` end, `/usage`
  show usage summary, `/context` show the chat context, `/g`/`/gc`/`/global`
  show the global-memory snapshot (`build_global_context`), `/chats` list
  chats, `/select chat <id>` resume another chat (closing the old usage
  session), `/select model <provider> <name>` persist a provider/model switch,
  `/settings` show the active selection, `/tools` list tools.
- `/select model` validates provider (must be in `utils.providers.PROVIDERS`),
  checks `GEMINI_API_KEY` for gemini models (billed via the gemini or openai
  provider) and an installed `model.gguf` for local, confirms a router
  `OPENAI_API_KEY`/`OPENROUTER_API_KEY` for non-gemini openai models,
  persists the choice via `SettingsService` (settings table), calls
  `reset_llm()`, and reopens the chat's usage session
  (`Chat.reopen_usage`) so accounting matches the new provider/model. The
  next `get_llm()` builds the new provider (overrides are applied to env by
  `SettingsService.apply_to_env()`). `.env` remains the default. Without an
  active chat the switch is persisted but no session is opened yet.
- Titles: the first `send()`/`send_stream()` on an untitled chat generates
  and persists a short title (LLM, falling back to the first user message —
  see `api.md`); `/chats` then shows it instead of "(untitled)".
- `--chat <id>` resumes a given chat instead of creating a new one
  (verbalized as "Chat #<id> …" in interactive mode).
- Prints registered tool names on startup.
- On exit (wrapped in `try/finally`): prints a usage summary (session token
  counts + cost, chat totals, global totals, per-model breakdown), then
  `chat.close()` stamps the session `ended_at`.

## Adding an interface

1. Create `apps/<name>/` with an entry `main()`.
2. Use `apps.base.Chat` (`Chat.create()` then `chat.send()`).
3. Wire dispatch in root `main.py`; if not built yet, keep it raising
   `NotImplementedError`.