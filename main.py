import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.env import ENV  # noqa: E402

ENV.init()


def main() -> None:
  parser = argparse.ArgumentParser(
    prog="llm-ccp",
    description="Local project management assistant.",
  )
  parser.add_argument(
    "app",
    nargs="?",
    default="cmd",
    help="Interface to launch: cmd | web | api | desktop (only cmd for now).",
  )
  parser.add_argument(
    "args",
    nargs=argparse.REMAINDER,
    help="Additional arguments forwarded to the selected interface.",
  )

  args = parser.parse_args()

  if args.app == "cmd":
    from apps.cmd.main import main as cmd_main

    sys.argv = [sys.argv[0], *args.args]
    cmd_main()
    return

  raise NotImplementedError(
    f"The '{args.app}' interface is not implemented yet. "
    "Available interfaces: cmd."
  )


if __name__ == "__main__":
  main()