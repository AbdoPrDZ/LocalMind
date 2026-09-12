from pathlib import Path

from apps.base import (
  CONTEXT_TAG_RE,
  _extract_context,
  _merge_contexts,
  _stream_strip_context,
  CONTEXT_INSTRUCTIONS,
)


def _stream_chunks(*words: str) -> list[str]:
  """Concatenate the given tokens and stream them with chunk-boundary splits.

  Splits inside the middle of every tag so partial-tag handling is exercised.
  """
  import re

  text = "".join(words)
  # Break the text at every 7th character → tags land split across chunks.
  return [text[i:i + 7] for i in range(0, len(text), 7)]


# ---------------------------------------------------------------------------
# _extract_context (non-streaming)
# ---------------------------------------------------------------------------


def test_extract_context_returns_clean_answer_and_new_context():
  answer = "You have 3 projects.\n<context>User asked about projects.</context>"
  new_context, clean = _extract_context(answer)

  assert new_context == "User asked about projects."
  assert clean == "You have 3 projects."


def test_extract_context_is_case_insensitive():
  answer = "Sure.\n<Context>Mixed case tags.</CONTEXT>"
  new_context, clean = _extract_context(answer)

  assert new_context == "Mixed case tags."
  assert clean == "Sure."


def test_extract_context_none_when_absent():
  assert _extract_context("just an answer") == (None, "just an answer")


def test_extract_context_strips_code_fenced_block():
  answer = "Done.\n```xml\n<context>fenced</context>\n```"
  new_context, clean = _extract_context(answer)

  assert new_context == "fenced"
  # The tags are gone; only an empty fence remains (the prompt forbids fencing).
  assert "context" not in clean.lower()
  assert "Done." in clean
  assert "<" not in clean.replace("```", "")


# ---------------------------------------------------------------------------
# _stream_strip_context (streaming)
# ---------------------------------------------------------------------------


def test_stream_strip_context_works_on_plain_reply():
  chunks = iter(["just", " an", " answer"])
  context_out: list = [None]
  assert "".join(_stream_strip_context(chunks, context_out)) == "just an answer"
  assert context_out[0] is None


def test_stream_strip_context_removes_tags_across_chunks():
  chunks = _stream_chunks(
    "You have 3 projects. ",
    "<context>",
    "User asked about projects.",
    "</context>",
  )
  context_out: list = [None]
  visible = "".join(_stream_strip_context(iter(chunks), context_out))

  assert context_out[0] == "User asked about projects."
  assert visible == "You have 3 projects. "
  assert "context" not in visible.lower()


def test_stream_strip_context_handles_mixed_case_tags():
  chunks = _stream_chunks(
    "Sure. ",
    "<Context>",
    "Mixed case tags.",
    "</CONTEXT>",
  )
  context_out: list = [None]
  visible = "".join(_stream_strip_context(iter(chunks), context_out))

  assert context_out[0] == "Mixed case tags."
  assert visible == "Sure. "


def test_stream_strip_context_handles_unclosed_tag():
  chunks = _stream_chunks(
    "Answer text ",
    "<context>",
    "never closed",
  )
  context_out: list = [None]
  visible = "".join(_stream_strip_context(iter(chunks), context_out))

  assert context_out[0] == "never closed"
  assert visible == "Answer text "


def test_stream_strip_context_idempotent_with_non_streaming():
  raw = "You have 3 projects.\n<context>User asked about projects.</context>"
  context_out: list = [None]
  visible = "".join(_stream_strip_context(iter(_stream_chunks(raw)), context_out))
  new_context, clean = _extract_context(raw)

  assert context_out[0] == new_context
  assert visible.strip() == clean


def test_context_tag_re_matches_mixed_case():
  assert CONTEXT_TAG_RE.search("<Context>hi</CONTEXT>").group(1) == "hi"


# ---------------------------------------------------------------------------
# Silence: the model is told never to announce context/memory bookkeeping
# ---------------------------------------------------------------------------


def test_context_instructions_forbid_announcing_updates():
  assert "Never mention" in CONTEXT_INSTRUCTIONS
  assert "exactly the lowercase tags" in CONTEXT_INSTRUCTIONS


def test_system_prompt_forbids_narrating_memory_saves():
  prompt = Path("resources/SYSTEM_PROMPT.md").read_text(encoding="utf-8")
  assert "never tell the user you are doing" in prompt


# ---------------------------------------------------------------------------
# _merge_contexts: the context accumulates, it never replaces
# ---------------------------------------------------------------------------


def test_merge_contexts_uses_first_update_when_empty():
  assert _merge_contexts(None, "The chat started with projects.") == "The chat started with projects."
  assert _merge_contexts("", "Something about tasks.") == "Something about tasks."


def test_merge_contexts_appends_new_topic_and_keeps_old():
  merged = _merge_contexts(
    "The chat started with the user asking about projects.",
    "Then the user asked about the GitHub profile.",
  )
  assert "started with the user asking about projects" in merged
  assert "asked about the GitHub profile" in merged
  # Chronological order: old first, new appended.
  assert merged.index("projects") < merged.index("GitHub profile")


def test_merge_contexts_replaces_when_update_contains_full_previous():
  previous = "The chat started with the user asking about projects."
  update = (
    "The chat started with the user asking about projects. Then the user asked "
    "about the GitHub profile. Then about memory."
  )
  assert _merge_contexts(previous, update) == update


def test_merge_contexts_deduplicates_exact_lines():
  previous = "topic one"
  update = "topic one\nAnother new topic"
  merged = _merge_contexts(previous, update)
  assert merged.count("topic one") == 1
  assert "Another new topic" in merged


def test_merge_contexts_respects_budget():
  previous = "old " * 9_000
  tail = "new topic at the end"
  merged = _merge_contexts(previous, tail)
  assert len(merged) <= 12_000
  assert "new topic at the end" in merged