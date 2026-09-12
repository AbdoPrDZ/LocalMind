import os

import pytest


@pytest.fixture()
def workspace(monkeypatch, tmp_path):
  monkeypatch.setenv("ALLOWED_PLACES", f"docs={tmp_path}")
  return tmp_path


@pytest.fixture()
def build_files(workspace):
  from tools.files import build_files_tools

  return {tool.name: tool for tool in build_files_tools()}


# --------------------------------------------------------------------------
# File tools
# --------------------------------------------------------------------------


def test_write_read_list_roundtrip(build_files, workspace):
  written = build_files["write_file"].call({
    "place": "docs",
    "path": "notes/hello.txt",
    "content": "hello world",
  })
  assert written["success"] is True
  assert (workspace / "notes" / "hello.txt").is_file()  # dirs auto-created

  read = build_files["read_file"].call({"place": "docs", "path": "notes/hello.txt"})
  assert read["content"] == "hello world"

  listing = build_files["list_dir"].call({
    "place": "docs",
    "path": ".",
    "pattern": "**/*.txt",
    "recursive": True,
  })
  assert listing["count"] == 1
  assert listing["entries"][0]["name"] == "notes/hello.txt"


def test_write_append(build_files, workspace):
  build_files["write_file"].call({
    "place": "docs", "path": "log.txt", "content": "one\n",
  })
  build_files["write_file"].call({
    "place": "docs", "path": "log.txt", "content": "two\n", "append": True,
  })
  assert (workspace / "log.txt").read_text() == "one\ntwo\n"


def test_path_escape_rejected(build_files, tmp_path):
  result = build_files["read_file"].call({
    "place": "docs",
    "path": os.path.join("..", "..", "..", "etc", "passwd"),
  })
  assert "error" in result
  assert "escapes" in result["error"]


def test_unknown_place_rejected(build_files):
  from pydantic import ValidationError

  with pytest.raises(ValidationError):
    build_files["read_file"].call({"place": "nope", "path": "x.txt"})


def test_schema_exposes_place_literal(build_files):
  schema = build_files["read_file"].input_model
  fields = schema.model_fields
  assert "place" in fields
  allowed = fields["place"].annotation.__args__
  assert list(allowed) == ["docs"]


# --------------------------------------------------------------------------
# Web helpers (pure)
# --------------------------------------------------------------------------


def test_extract_html_text():
  from tools.web import extract_html_text

  html = (
    "<html><head><title>skip</title><style>a{color:red}</style></head>"
    "<body><script>alert(1)</script>"
    "<h1>Hello</h1><p>Some <b>content</b> here.</p>"
    "<ul><li>One</li><li>Two</li></ul></body></html>"
  )
  text = extract_html_text(html)
  assert "Hello" in text
  assert "Some content here." in " ".join(text.split())
  assert "alert" not in text
  assert "a{color:red}" not in text


def test_parse_rss_items():
  from tools.web import parse_rss_items

  xml_text = """<?xml version="1.0"?><rss><channel><item>
    <title>First</title><link>https://example.com/a</link>
    <description>Some snippet </description></item>
    <item><title>No link</title><description>zzz</description></item>
    <item><title>Second</title><link>https://example.com/b</link></item>
  </channel></rss>"""
  items = parse_rss_items(xml_text, limit=5)
  assert len(items) == 2
  assert items[0]["url"] == "https://example.com/a"
  assert items[1]["title"] == "Second"


def test_fetch_page_rejects_non_http():
  from tools.web import FetchPageTool

  result = FetchPageTool().call({"url": "file:///etc/passwd"})
  assert "error" in result
  assert "http(s)" in result["error"]


# --------------------------------------------------------------------------
# System tools
# --------------------------------------------------------------------------


def test_current_datetime():
  from tools.system import CurrentDatetimeTool, _CurrentDatetimeInput

  tool = CurrentDatetimeTool()
  tool.input_model = _CurrentDatetimeInput

  out = tool.call({"timezone": "UTC"})
  assert out["datetime"].endswith("+00:00")
  assert out["timezone"] == "UTC"

  bad = tool.call({"timezone": "Mars/Olympus"})
  assert "error" in bad


def test_system_info():
  from tools.system import SystemInfoTool

  out = SystemInfoTool().call({})
  assert out["os"]
  assert out["python_version"]


# --------------------------------------------------------------------------
# Ask-user tool
# --------------------------------------------------------------------------


def test_ask_with_handler():
  from tools.ask import build_ask_tools

  tool = build_ask_tools(lambda q, options, allow_free_text: "42")[0]
  assert tool.call({"question": "What is the answer?"}) == {"answer": "42"}


def test_ask_no_handler_is_error():
  from tools.ask import build_ask_tools

  tool = build_ask_tools(None)[0]
  result = tool.call({"question": "hi"})
  assert "error" in result
  assert "handler" in result["error"]


def test_ask_dismissed_is_error():
  from tools.ask import build_ask_tools

  tool = build_ask_tools(lambda q, options, allow_free_text: None)[0]
  result = tool.call({"question": "hi"})
  assert "error" in result


# --------------------------------------------------------------------------
# Shell tool
# --------------------------------------------------------------------------


def test_shell_disabled_by_default(monkeypatch):
  monkeypatch.setenv("ENABLE_SHELL_TOOLS", "0")
  from tools.shell import build_shell_tools

  tool = build_shell_tools(None)[0]
  result = tool.call({"command": "echo hi", "description": "say hi"})
  assert "error" in result
  assert "DISABLED" in result["error"]


def test_shell_needs_handler(monkeypatch, workspace):
  monkeypatch.setenv("ENABLE_SHELL_TOOLS", "1")
  from tools.shell import build_shell_tools

  tool = build_shell_tools(None)[0]
  result = tool.call({"command": "echo hi", "description": "say hi"})
  assert "error" in result
  assert "handler" in result["error"]


def test_shell_user_cancels(monkeypatch, tmp_path):
  monkeypatch.setenv("ENABLE_SHELL_TOOLS", "1")
  monkeypatch.setenv("ALLOWED_PLACES", f"workspace={tmp_path}")
  from tools.shell import build_shell_tools

  tool = build_shell_tools(lambda q, options, allow_free_text: "No, cancel")[0]
  result = tool.call({"command": "echo unsafe", "description": "RULE"})
  assert result == {"cancelled": "The user did not approve running the command."}


def test_shell_runs_after_approval(monkeypatch, tmp_path):
  monkeypatch.setenv("ENABLE_SHELL_TOOLS", "1")
  monkeypatch.setenv("ALLOWED_PLACES", f"workspace={tmp_path}")
  from tools.shell import build_shell_tools

  tool = build_shell_tools(lambda q, options, allow_free_text: "Yes, run it")[0]
  result = tool.call({"command": "echo hello", "description": "say hi"})
  assert result["exit_code"] == 0
  assert "hello" in result["stdout"]


# --------------------------------------------------------------------------
# Notes CRUD via the generic tools
# --------------------------------------------------------------------------


def test_note_crud_tools_present(db):
  from tools.model import build_crud_tools

  names = [tool.name for tool in build_crud_tools()]
  for expected in ("create_notes", "get_notes", "list_notes", "update_notes", "delete_notes"):
    assert expected in names


def test_note_create_get_list(db):
  from tools.model import build_crud_tools

  tools = {tool.name: tool for tool in build_crud_tools()}
  created = tools["create_notes"].call({"title": "Todo", "content": "Buy milk", "tags": "errands"})
  assert created["success"] is True
  note_id = created["row"]["id"]

  fetched = tools["get_notes"].call({"record_id": note_id})
  assert fetched["title"] == "Todo"
  assert fetched["tags"] == "errands"

  rows = tools["list_notes"].call({"limit": 10})
  assert len(rows) == 1
  assert rows[0]["content"] == "Buy milk"