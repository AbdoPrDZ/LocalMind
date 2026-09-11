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
2. `chat.send(text)` saves the user message, builds `[system(+context), user]`.
3. `Agent.run(messages)` sends the system prompt + current chat context + the
   new user message with tool schemas to the model (NOT the full history).
4. Model may reply with tool calls (native or Qwen3 `<tool_call>` XML blocks).
5. Agent executes each call, appends a `tool` role message with JSON results.
6. Loop until the model returns a plain-text answer; `clean_answer()` strips the
   `thinking` preamble.
7. If the reply contains a `<context>...</context>` block, `_extract_context()`
   saves the new summary as the chat context and removes the tags; the cleaned
   assistant reply is saved and returned.

Streaming variant: `chat.send_stream(text)` calls `Agent.run_stream()`; the
answer is yielded token-by-token (only text after the `response` marker for
Qwen3, whole answer chunked otherwise). `_stream_strip_context()` strips any
`<context>...</context>` block from the stream and the full reply + context are
saved on completion.

## CMD interface details (`apps/cmd/main.py`)

- No prompt argument → `questionary.text` interactive loop; `exit`/`quit`
  (or Ctrl+C) ends it.
- With a prompt argument → single answer, then exits.
- `--chat <id>` resumes a given chat instead of creating a new one
  (verbalized as "Chat #<id> …" in interactive mode).
- Prints registered tool names on startup.

## Adding an interface

1. Create `apps/<name>/` with an entry `main()`.
2. Use `apps.base.Chat` (`Chat.create()` then `chat.send()`).
3. Wire dispatch in root `main.py`; if not built yet, keep it raising
   `NotImplementedError`.