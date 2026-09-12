"""System utilities for the LLM: time, machine info, and the clipboard.

Interface-agnostic and dependency-light: platform info comes from the stdlib
(``psutil`` is used for memory/disk only when it is installed), and clipboard
access shells out to Windows PowerShell (no third-party packages).
"""

import os
import platform
import subprocess
import sys
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from utils.tool import Tool


class _CurrentDatetimeInput(BaseModel):
  timezone: Optional[str] = Field(
    default=None,
    description=(
      "IANA timezone name (e.g. Europe/Paris, America/New_York). "
      "Omit for the machine's local time."
    ),
  )


class _SystemInfoInput(BaseModel):
  pass


class CurrentDatetimeTool(Tool):
  name = "current_datetime"
  description = (
    "Return the current date and time, in the given IANA timezone or the "
    "machine's local time. Use it to reason about dates, deadlines, and age "
    "of timestamps."
  )
  input_model = _CurrentDatetimeInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    tz_name = arguments.get("timezone")
    try:
      if tz_name:
        now = datetime.now(ZoneInfo(tz_name))
        tz_label = tz_name
      else:
        now = datetime.now().astimezone()
        tz_label = now.tzname() or "local"
    except ZoneInfoNotFoundError:
      return {"error": f"Unknown timezone '{tz_name}'."}

    return {
      "datetime": now.isoformat(timespec="seconds"),
      "timezone": tz_label,
      "weekday": now.strftime("%A"),
    }


class SystemInfoTool(Tool):
  name = "get_system_info"
  description = (
    "Return basic information about the machine running LocalMind: OS, "
    "architecture, CPU count, Python version, working directory, and — when "
    "psutil is installed — memory and disk usage."
  )
  input_model = _SystemInfoInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    info: dict[str, Any] = {
      "hostname": platform.node(),
      "os": platform.system(),
      "os_release": platform.release(),
      "architecture": platform.machine(),
      "processor": platform.processor() or None,
      "cpu_count": os.cpu_count(),
      "python_version": sys.version.split()[0],
      "python_executable": sys.executable,
      "cwd": os.getcwd(),
    }

    try:
      import psutil  # optional

      mem = psutil.virtual_memory()
      info["memory_total_gb"] = round(mem.total / (1024**3), 2)
      info["memory_used_gb"] = round(mem.used / (1024**3), 2)
      info["memory_percent"] = mem.percent
      disk = psutil.disk_usage(os.getcwd())
      info["disk_total_gb"] = round(disk.total / (1024**3), 2)
      info["disk_free_gb"] = round(disk.free / (1024**3), 2)
    except ImportError:
      info["memory_and_disk"] = "Install psutil to include memory/disk usage."

    return info


# Below: clipboard tools (Windows PowerShell bridge).


class _ClipboardGetInput(BaseModel):
  max_chars: int = Field(default=10_000, ge=1, le=200_000, description="Max characters to return.")


class ClipboardGetTool(Tool):
  name = "clipboard_get"
  description = "Read the current clipboard content (Windows)."
  input_model = _ClipboardGetInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    try:
      text = _clipboard_text("Get-Clipboard")
    except (OSError, subprocess.SubprocessError) as exc:
      return {"error": f"Clipboard read failed: {exc}"}
    return {"content": (text or "")[: arguments["max_chars"]]}


class _ClipboardSetInput(BaseModel):
  text: str = Field(description="Text to place on the clipboard.")


class ClipboardSetTool(Tool):
  name = "clipboard_set"
  description = "Put text on the clipboard (Windows)."
  input_model = _ClipboardSetInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    try:
      _clipboard_text("Set-Clipboard", input_text=arguments["text"])
    except (OSError, subprocess.SubprocessError) as exc:
      return {"error": f"Clipboard write failed: {exc}"}
    return {"success": True, "bytes": len(arguments["text"])}


def _clipboard_text(command: str, input_text: str | None = None) -> str:
  """Run a PowerShell clipboard command, honoring UTF-8 on both directions."""
  if sys.platform != "win32":
    raise OSError("Clipboard tools currently work only on Windows.")

  script = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    f"{command}"
  )
  result = subprocess.run(
    ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    input=input_text,
    timeout=15,
  )
  if result.returncode != 0:
    raise subprocess.SubprocessError(
      result.stderr.strip() or f"PowerShell '{command}' failed."
    )
  return result.stdout.rstrip("\r\n")


def build_system_tools() -> list[Tool]:
  return [CurrentDatetimeTool(), SystemInfoTool(), ClipboardGetTool(), ClipboardSetTool()]