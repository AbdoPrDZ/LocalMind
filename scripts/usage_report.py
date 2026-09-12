"""Print usage/cost report: global totals, per chat, and per model.

Usage is tracked per chat session in the ``usage`` table (see
``services/usage.py``). Run from the project root:

    python scripts/usage_report.py
"""

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from utils.env import ENV  # noqa: E402

ENV.init()

import argparse  # noqa: E402

from database import init_db  # noqa: E402
from services.usage import UsageService  # noqa: E402


def _fmt_cost(cost: float) -> str:
  return f"${cost:.6f}"


def _fmt_tokens(count) -> str:
  return f"{int(count):,}"


def main() -> None:
  parser = argparse.ArgumentParser(
    prog="usage-report",
    description="Show token/cost usage tracked per chat session.",
  )
  parser.add_argument(
    "--chat",
    type=int,
    default=None,
    help="Limit the report to one chat id.",
  )
  args = parser.parse_args()

  init_db()

  totals = UsageService.totals()
  print("Usage report")
  print("=" * 52)
  print(
    f"  sessions       {totals['sessions']}\n"
    f"  total tokens   {_fmt_tokens(totals['total_tokens'])}\n"
    f"    prompt       {_fmt_tokens(totals['prompt_tokens'])}\n"
    f"    completion   {_fmt_tokens(totals['completion_tokens'])}\n"
    f"  estimated cost {_fmt_cost(totals['cost'])}"
  )

  chats = (
    [item for item in UsageService.totals_by_chat() if item["chat_id"] == args.chat]
    if args.chat is not None
    else UsageService.totals_by_chat()
  )

  print("\nPer chat")
  print("=" * 52)
  if not chats:
    print("  (no usage recorded)")
  for row in chats:
    print(
      f"  chat #{row['chat_id']:<6}"
      f"{_fmt_tokens(row['total_tokens']):>10} tokens"
      f" {_fmt_cost(row['cost']):>12}"
      f" ({row['sessions']} sessions)"
    )

  print("\nPer model")
  print("=" * 52)
  models = UsageService.totals_by_model()
  if not models:
    print("  (no usage recorded)")
  for row in models:
    print(
      f"  {row['provider']:<8} {row['model']:<28}"
      f"{_fmt_tokens(row['total_tokens']):>10} tokens"
      f" {_fmt_cost(row['cost']):>12}"
    )


if __name__ == "__main__":
  main()