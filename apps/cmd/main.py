import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import questionary

from apps.base import Chat


def print_answer(chat: Chat, prompt: str) -> None:
  print("\nAssistant:")
  for chunk in chat.send_stream(prompt):
    sys.stdout.write(chunk)
    sys.stdout.flush()
  print()


def run_once(prompt: str) -> None:
  chat = Chat.create()
  print_answer(chat, prompt)


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


def interactive(chat: Chat) -> None:
  print(f"Registered tools: {[t.name for t in chat.tools]}")
  print(f"Chat #{chat.id} — type 'exit' or 'quit' to stop.\n")

  while True:
    answer = _ask()

    if answer is None:
      break

    prompt = answer.strip()

    if prompt.lower() in {"exit", "quit"}:
      break

    if not prompt:
      continue

    print_answer(chat, prompt)


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

  chat = Chat.load(args.chat) if args.chat is not None else Chat.create()

  if args.prompt:
    print_answer(chat, args.prompt)
  else:
    interactive(chat)


if __name__ == "__main__":
  main()