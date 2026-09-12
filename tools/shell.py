"""Shell tool: run a command, but only after the user approves it.

Safety model:
- Gated behind ``ENABLE_SHELL_TOOLS=1`` in ``.env`` — otherwise the tool
  refuses to run anything.
- Every run requires the model to give a short ``description`` of what the
  command does, then asks the user for confirmation via the app's question
  handler. No confirmation → the command never executes.
- ``cwd`` must stay inside one of the ``ALLOWED_PLACES`` folders (see
  ``tools/files.py``). Output is capped.
"""

import os
import subprocess
from typing import Any, Optional

from pydantic import BaseModel, Field

from tools.ask import QuestionHandler
from tools.files import _contained, _roots
from utils.env import ENV
from utils.tool import Tool

_MAX_OUTPUT_CHARS = 100_000


class _RunCommandInput(BaseModel):
  command: str = Field(
    description="The shell command to run (shell syntax as-is)."
  )
  description: str = Field(
    description=(
      "A short (1-2 sentences) plain-language description of what this command "
      "does and why. Shown to the user who must approve the execution."
    )
  )
  cwd: str = Field(
    default=".",
    description="Working directory for the command, relative to an allowed folder.",
  )
  timeout: int = Field(default=30, ge=1, le=120, description="Timeout in seconds.")
  max_output_chars: int = Field(
    default=8_000,
    ge=500,
    le=_MAX_OUTPUT_CHARS,
    description="Max characters of combined output to return.",
  )


class _ShellEnabledMixin:
  def _enabled(self) -> str | None:
    if (ENV.get("ENABLE_SHELL_TOOLS", default="0") or "0").strip() not in {"1", "true", "yes"}:
      return (
        "SHELL TOOLS ARE DISABLED. Set ENABLE_SHELL_TOOLS=1 in .env to allow "
        "running commands. Until then, explain the command you would run."
      )
    return None


class RunCommandTool(_ShellEnabledMixin, Tool):
  name = "run_command"
  description = (
    "Run a shell command inside the allowed folders after asking the user for "
    "approval. Provide a short `description` of the command — the user must "
    "confirm it before it executes. Requires ENABLE_SHELL_TOOLS=1 in .env."
  )
  input_model = _RunCommandInput

  def __init__(self, handler: Optional[QuestionHandler] = None) -> None:
    self.handler = handler

  def execute(self, arguments: dict[str, Any]) -> Any:
    disabled = self._enabled()
    if disabled:
      return {"error": disabled}

    if self.handler is None:
      return {
        "error": (
          "No question handler is configured, so the user cannot approve this "
          "command. Refuse to run it and explain what you intended to do."
        )
      }

    approved = self._ask_approval(arguments["description"], arguments["command"])
    if not approved:
      return {"cancelled": "The user did not approve running the command."}

    cwd = self._resolve_cwd(arguments.get("cwd") or ".")
    if cwd is None:
      return {"error": "The requested working directory escapes the allowed folders."}

    try:
      result = subprocess.run(
        arguments["command"],
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=arguments["timeout"],
        encoding="utf-8",
        errors="replace",
      )
    except subprocess.TimeoutExpired:
      return {"error": f"Command timed out after {arguments['timeout']}s."}
    except OSError as exc:
      return {"error": f"Failed to run command: {exc}"}

    max_chars = arguments["max_output_chars"]
    return {
      "exit_code": result.returncode,
      "stdout": (result.stdout or "")[:max_chars],
      "stderr": (result.stderr or "")[:max_chars],
    }

  def _ask_approval(self, description: str, command: str) -> bool:
    answer = self.handler(
      (
        f"A command wants to run on your machine:\n\n"
        f"{command}\n\n"
        f"What it does: {description}\n\n"
        f"Approve execution?"
      ),
      options=["Yes, run it", "No, cancel"],
      allow_free_text=False,
    )
    return bool(answer and str(answer).strip().lower().startswith("y"))

  def _resolve_cwd(self, raw: str) -> str | None:
    for root in _roots():
      candidate = os.path.realpath(os.path.join(root, raw))
      if _contained(candidate, _roots()) and os.path.isdir(candidate):
        return candidate
    return None


def build_shell_tools(handler: Optional[QuestionHandler] = None) -> list[Tool]:
  """Instantiate the shell tool bound to an app-provided question handler."""
  return [RunCommandTool(handler)]