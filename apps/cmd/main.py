import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import questionary

from apps.base import Chat
from database import init_db
from services.global_context import build_global_context
from services.settings import SettingsService, resolve_model, resolve_provider
from utils.env import ENV
from utils.llm import reset_llm
from utils.providers import PROVIDERS

#: Slash commands recognized in interactive mode, mapped to handler names.
SLASH_COMMANDS = {
  "/h": "help",
  "/help": "help",
  "/q": "quit",
  "/quit": "quit",
  "/exit": "quit",
  "/usage": "usage",
  "/context": "context",
  "/g": "global",
  "/gc": "global",
  "/global": "global",
  "/global_context": "global",
  "/chats": "chats",
  "/select": "select",
  "/settings": "settings",
  "/tools": "tools",
}

HELP_TEXT = """Commands:
  /h, /help            show this help
  /q, /quit            end the session
  /usage               show token/cost usage for this chat and globally
  /context             show the running chat context summary
  /g, /gc, /global     show the global memory context (cross-chat knowledge)
  /chats               list all chats with message counts
  /select chat <id>    switch to another chat (resumes it)
  /select model <provider> <name>  switch LLM provider/model (persisted)
  /settings            show the active provider/model and overrides
  /tools               list the tools the assistant can call

Anything else is sent to the assistant as a prompt."""


def print_answer(chat: Chat, prompt: str) -> None:
  print("\nAssistant:")
  for chunk in chat.send_stream(prompt):
    sys.stdout.write(chunk)
    sys.stdout.flush()
  print()


def _print_reply(chat: Chat, prompt: str) -> None:
  """Stream an answer; a provider/LLM failure prints a short error instead of
  killing the session. The user message stays persisted either way."""
  try:
    print_answer(chat, prompt)
  except Exception as exc:  # noqa: BLE001 - keep the interactive loop alive
    print(f"\nError: {exc}")


def _print_usage_summary(chat: Chat) -> None:
  """Print the chat's usage for the current session, plus global totals."""
  summary = chat.usage_summary()
  session = summary["session"]

  print()
  if session is None:
    print(f"Usage: chat #{chat.id} — no tokens spent in this session.")
    print(f"  chat #{chat.id} total: {summary['chat_totals']['total_tokens']} tokens · ${summary['chat_totals']['cost']:.6f}")
    print(f"  global: {summary['global_totals']['total_tokens']} tokens · ${summary['global_totals']['cost']:.6f} "
          f"across {summary['global_totals']['sessions']} sessions")
    return

  print(
    f"Usage: chat #{chat.id} · {session['provider']} ({session['model']}) · "
    f"{session['prompt_tokens']} in / {session['completion_tokens']} out tokens · "
    f"${session['cost']:.6f}"
  )
  print(
    f"  chat #{chat.id} total: {summary['chat_totals']['total_tokens']} tokens · "
    f"${summary['chat_totals']['cost']:.6f} (all sessions)"
  )
  print(
    f"  global: {summary['global_totals']['total_tokens']} tokens · "
    f"${summary['global_totals']['cost']:.6f} across {summary['global_totals']['sessions']} sessions"
  )
  for row in summary["by_model"]:
    print(f"  by model: {row['model']} · {row['total_tokens']} tokens · ${row['cost']:.6f}")


def _print_chats() -> None:
  chats = Chat.list_chats()
  if not chats:
    print("\nNo chats yet.")
    return
  print("\nChats:")
  for row in chats:
    title = row["title"] or "(untitled)"
    stamp = row["created_at"].strftime("%Y-%m-%d %H:%M")
    print(f"  #{row['id']:<4} {stamp}  {row['messages']} msg  {title}")
    preview = (row["context"] or "").replace("\n", " ").strip()
    if preview:
      print(f"        {preview[:72]}")


def _print_settings() -> None:
  provider = resolve_provider()
  model = resolve_model(provider)
  stored_provider = SettingsService.get_provider()
  stored_model = SettingsService.get_model(provider)

  print(f"\nProvider : {provider}" + (f"  (overridden)" if stored_provider else "  (.env default)"))
  print(f"Model    : {model}" + (f"  (overridden)" if stored_model else "  (.env default)"))
  if provider == "gemini":
    try:
      ENV.get_gemini_api_key()
      print("API key  : set")
    except ValueError:
      print("API key  : NOT SET — set LLM_GEMINI_API_KEY in .env before using Gemini")
  elif provider == "openai":
    if model.strip().lower().startswith("gemini"):
      try:
        ENV.get_gemini_api_key()
        print("API key  : set (Gemini — LLM_GEMINI_API_KEY)")
      except ValueError:
        print("API key  : NOT SET — set LLM_GEMINI_API_KEY in .env before using Gemini models")
    else:
      print("API key  : " + ("set (free router)" if (ENV.get("LLM_OPENAI_API_KEY") or ENV.get("LLM_OPENROUTER_API_KEY")) else "NOT SET — set LLM_OPENAI_API_KEY or LLM_OPENROUTER_API_KEY in .env"))
  elif provider == "free":
    print("API key  : not required (keyless endpoint)")
  if stored_provider:
    print("\nUse /select model to change, or clear the settings row to revert to .env.")


def _select_model(provider: str, model: str) -> str | None:
  """Persist a provider/model switch. Returns an error message on failure, None on success."""
  provider = provider.strip().lower()

  if provider not in PROVIDERS:
    return f"Unknown provider '{provider}'. Available: {', '.join(PROVIDERS)}."

  if not model:
    return "Usage: /select model <provider> <name>"

  if provider == "gemini":
    try:
      ENV.get_gemini_api_key()
    except ValueError as exc:
      return str(exc)
  elif provider == "openai":
    if model.strip().lower().startswith("gemini"):
      try:
        ENV.get_gemini_api_key()
      except ValueError as exc:
        return str(exc)
    else:
      api_key = ENV.get("LLM_OPENAI_API_KEY") or ENV.get("LLM_OPENROUTER_API_KEY")
      if not api_key:
        return "Set LLM_OPENAI_API_KEY (or LLM_OPENROUTER_API_KEY) in .env before using free models via LLM_PROVIDER=openai."
  elif provider == "free":
    from utils.providers.free import available_models

    if model.strip().lower() not in available_models() and not ENV.get("LLM_FREE_BASE_URL"):
      return f"Model '{model}' is not a keyless model of this endpoint. Available: {', '.join(available_models())}."
  else:
    try:
      path = os.path.join(ENV.get_models_dir(), model, "model.gguf")
    except ValueError as exc:
      return str(exc)
    if not os.path.exists(path):
      return f"Model '{model}' is not installed. Run: python scripts/install_model.py {model}"

  SettingsService.set_provider(provider)
  SettingsService.set_model(provider, model)
  reset_llm()
  return None


def _handle_select(chat: Chat | None, parts: list[str]) -> Chat | None:
  """Handle ``/select chat <id>`` and ``/select model <provider> <name>``.

  Returns the (possibly switched) chat — ``None`` until a real message starts
  one. Model switches persist to the settings table and reload the provider;
  chat switches resume another chat (the old usage session is closed when one
  was open).
  """
  if len(parts) >= 3 and parts[1].lower() == "chat":
    try:
      chat_id = int(parts[2])
    except ValueError:
      print("Usage: /select chat <id>")
      return chat
    try:
      switched = Chat.load(chat_id, question_handler=_ask_value)
    except ValueError as exc:
      print(exc)
      return chat
    if chat is not None:
      chat.close()
    print(f"Switched to chat #{switched.id}")
    return switched

  if len(parts) >= 4 and parts[1].lower() == "model":
    provider = parts[2]
    model = parts[3]
    error = _select_model(provider, model)
    if error:
      print(error)
      return chat
    new_provider = resolve_provider()
    new_model = resolve_model(new_provider)
    if chat is not None:
      chat.reopen_usage(new_provider, new_model)
    print(f"Switched model to {new_provider} ({new_model})")
    return chat

  print("Usage: /select chat <id> | /select model <provider> <name>")
  return chat


def _ask() -> str | None:
  """Prompt the user, using questionary on a real console and plain input() otherwise.

  questionary/prompt_toolkit needs an interactive Win32 console; when running
  with output captured (IDE run pane, piped subprocess, etc.) it raises
  ``NoConsoleScreenBufferError``. Fall back to a plain prompt in that case.
  """
  try:
    if sys.stdin.isatty() and sys.stdout.isatty():
      return questionary.text(
        "You",
        qmark=">",
        style=questionary.Style.from_dict({
          "qmark": "fg:ansigreen bold",
          "answer": "bold",
        }),
      ).ask()
    return input("You: ")
  except (KeyboardInterrupt, EOFError):
    print()
    return None


def _ask_value(prompt: str, options: list[str] | None, allow_free_text: bool) -> str | None:
  """Question handler for the ``ask_user``/``run_command`` tools.

  Backs the generic UI loop with questionary prompts (plain input() when not
  on a real console). Returns the user's answer or ``None`` when they abort.
  """
  try:
    if options:
      return questionary.select(
        prompt,
        choices=options,
        qmark="?",
        pointer="»",
      ).ask()
    if not allow_free_text:
      return None
    if sys.stdin.isatty() and sys.stdout.isatty():
      return questionary.text(prompt, qmark="?").ask()
    return input(f"{prompt} ")
  except (KeyboardInterrupt, EOFError):
    print()
    return None


def interactive(chat: Chat | None) -> Chat | None:
  current = chat

  while True:
    answer = _ask()

    if answer is None:
      break

    prompt = answer.strip().lstrip("\ufeff")

    if not prompt:
      continue

    if prompt.lower() in {"exit", "quit"}:
      break

    parts = prompt.split()
    first = parts[0].lower()
    command = SLASH_COMMANDS.get(first)

    if first.startswith("/") and command is None:
      print(f"\nUnknown command: {first} — type '/h' for help.")
      continue

    if command == "help":
      print(f"\n{HELP_TEXT}")
      continue
    if command == "quit":
      break
    if command == "chats":
      _print_chats()
      continue
    if command == "settings":
      _print_settings()
      continue
    if command == "select":
      current = _handle_select(current, parts)
      continue
    if command in {"usage", "context", "global", "tools"}:
      if current is None:
        print("\nNo chat yet — send a message to start one.")
        continue
      if command == "usage":
        _print_usage_summary(current)
      elif command == "context":
        print(f"\nChat context: {current.context or 'no context yet'}")
      elif command == "global":
        snapshot = build_global_context(current_chat_id=current.id)
        print(f"\nGlobal context: {snapshot or 'no global memories yet'}")
      else:
        print(f"\nRegistered tools: {[t.name for t in current.tools]}")
      continue

    if current is None:
      current = Chat.create(question_handler=_ask_value)
      provider = resolve_provider()
      model = resolve_model(provider)
      print(f"\nChat #{current.id} · model: {provider} ({model})")
      print(f"Registered tools: {[t.name for t in current.tools]}")
    _print_reply(current, prompt)

  return current


def main() -> None:
  parser = argparse.ArgumentParser(
    prog="LocalMind cmd",
    description="Local project management assistant (command line).",
  )
  parser.add_argument(
    "prompt",
    nargs="?",
    help="Ask a single question and exit (no argument starts interactive mode).",
  )
  parser.add_argument(
    "--chat",
    type=int,
    default=None,
    help="Resume an existing chat by id instead of creating a new one.",
  )

  args = parser.parse_args()

  # The database may not exist yet (a fresh run after removing the file): the
  # lazy chat creation no longer forces an init via Chat.create(), so do it
  # here — commands like /chats or /usage hit the tables before any chat.
  init_db()

  # A chat row is only created for a real message: single-shot mode or the
  # first non-command prompt in interactive mode.
  chat = (
    Chat.load(args.chat, question_handler=_ask_value)
    if args.chat is not None
    else None
  )

  try:
    if args.prompt:
      if chat is None:
        chat = Chat.create(question_handler=_ask_value)
      _print_reply(chat, args.prompt)
    else:
      if chat is not None:
        provider = resolve_provider()
        model = resolve_model(provider)
        print(f"\nChat #{chat.id} · model: {provider} ({model})")
        print(f"Registered tools: {[t.name for t in chat.tools]}")
      else:
        print("Type '/h' for commands or '/q' to quit.")
      chat = interactive(chat)
  finally:
    if chat is not None:
      _print_usage_summary(chat)
      chat.close()


if __name__ == "__main__":
  main()