"""Ranking utilities for query-aware retrieval (Phase 1: no vectors).

Scores blend keyword overlap (the main signal until Phase 3 adds FTS5) with
importance and recency. ``services/context/retrieval.py`` consumes these to
pick the few entries worth injecting into the prompt.
"""

import re
from datetime import datetime

_WORD_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset({
  "a", "an", "the", "and", "or", "but", "for", "nor", "on", "at", "to",
  "from", "by", "of", "in", "is", "are", "was", "were", "be", "been", "am",
  "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
  "them", "my", "your", "his", "its", "our", "their", "this", "that", "these",
  "those", "what", "which", "who", "whom", "when", "where", "how", "why",
  "do", "does", "did", "have", "has", "had", "will", "would", "can", "could",
  "should", "may", "might", "must", "shall", "with", "without", "as", "than",
  "so", "if", "then", "now", "please", "about", "some", "very", "just",
  "topic", "whats", "its", "thats", "dont", "doesnt",
})


def tokenize(text: str) -> list[str]:
  """Lowercased alphanumeric tokens (keeps unicode letters/digits)."""
  return _WORD_RE.findall((text or "").lower())


def keyword_overlap(query: str, text: str) -> float:
  """Fraction of meaningful query tokens also present in ``text`` (0..1)."""
  query_tokens = [t for t in tokenize(query) if t not in _STOPWORDS]
  if not query_tokens:
    return 0.0
  text_tokens = set(tokenize(text))
  matched = sum(1 for token in query_tokens if token in text_tokens)
  return matched / len(query_tokens)


def _recency(updated_at: datetime | str | None) -> float:
  """1.0 for now, decaying toward 0 with age in days (floor 0.1)."""
  if updated_at is None:
    return 0.1
  if isinstance(updated_at, str):
    try:
      updated_at = datetime.fromisoformat(updated_at)
    except ValueError:
      return 0.1
  try:
    if updated_at.tzinfo is not None:
      days = (datetime.now(updated_at.tzinfo) - updated_at).days
    else:
      days = (datetime.now() - updated_at).days
  except (TypeError, ValueError):
    return 1.0
  return 1.0 / (1.0 + max(days, 0))


def score_memory(
  query: str,
  content: str,
  importance: int,
  updated_at: datetime | str | None,
) -> float:
  """Keyword-heavy score; importance/recency only nudge the ordering."""
  keyword = keyword_overlap(query, content)
  importance_norm = max(importance, 1) / 4.0
  return 0.7 * keyword + 0.2 * importance_norm + 0.1 * _recency(updated_at)


def score_message(query: str, content: str, role: str = "assistant") -> float:
  """Score a past message: user messages count double (they asked for it)."""
  score = keyword_overlap(query, content)
  if role == "user":
    score *= 2.0
  return score