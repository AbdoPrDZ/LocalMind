"""Web tools: keyless web search (Bing RSS) and page-to-text reading.

Both tools are pure/offline-friendly HTTP calls via httpx: no vendor SDK, no
API key required. ``fetch_page`` returns readable text (scripts/styles stripped)
so the model can answer from real content; ``web_search`` returns title/url/
snippet candidates that ``fetch_page`` can then turn into full text.
"""

import html as html_lib
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, Field

from utils.env import ENV
from utils.tool import Tool

_TIMEOUT_SECONDS = 30.0
_MAX_FETCH_CHARS = 50_000

_USER_AGENT = (
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# --------------------------------------------------------------------------
# HTML → text (pure, unit-testable)
# --------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
  """Collect visible text: spacing between blocks, skipping scripts/styles."""

  _BLOCK_TAGS = {
    "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "pre", "code", "blockquote", "td", "tr", "article", "section", "table",
  }
  _SKIP_TAGS = {"script", "style", "noscript", "template", "head"}

  def __init__(self) -> None:
    super().__init__(convert_charrefs=True)
    self.parts: list[str] = []
    self._skip_depth = 0

  def handle_starttag(self, tag, attrs):
    if tag in self._SKIP_TAGS:
      self._skip_depth += 1
      return
    if tag in self._BLOCK_TAGS:
      self.parts.append("\n")

  def handle_endtag(self, tag):
    if tag in self._SKIP_TAGS and self._skip_depth > 0:
      self._skip_depth -= 1
      return
    if tag in self._BLOCK_TAGS:
      self.parts.append("\n")

  def handle_data(self, data):
    if self._skip_depth == 0 and data:
      self.parts.append(data)


def extract_html_text(raw: str) -> str:
  """Strip markup and return collapsed, readable text from an HTML document."""
  parser = _TextExtractor()
  parser.feed(raw or "")
  text = html_lib.unescape("".join(parser.parts))
  lines = [line.strip() for line in text.splitlines()]
  compact = [line for line in lines if line]
  return "\n".join(compact).strip() or "(no readable text found on this page)"


# --------------------------------------------------------------------------
# RSS search results parsing (pure, unit-testable)
# --------------------------------------------------------------------------


def parse_rss_items(xml_text: str, limit: int = 5) -> list[dict]:
  """Parse Bing-style RSS search output into ``{title, url, snippet}`` items."""
  import xml.etree.ElementTree as ET

  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError:
    return []

  items = []
  for item in root.iter("item"):
    title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    desc = (item.findtext("description") or "").strip()
    if not link:
      continue
    items.append({"title": title, "url": link, "snippet": desc[:300]})
    if len(items) >= limit:
      break
  return items


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


class _SearchInput(BaseModel):
  query: str = Field(description="The search query to run on the web.")
  max_results: int = Field(default=5, ge=1, le=10, description="Max results to return.")


class WebSearchTool(Tool):
  name = "web_search"
  description = (
    "Search the web (keyless, Bing RSS) and return title/url/snippet results. "
    "Use it for recent or factual information the model may not know, then call "
    "fetch_page on a promising URL for the full text."
  )
  input_model = _SearchInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    provider = (ENV.get("WEB_SEARCH_PROVIDER", default="bing") or "bing").strip().lower()
    if provider != "bing":
      return {"error": f"Unsupported WEB_SEARCH_PROVIDER '{provider}'. Supported: bing."}

    query = arguments["query"]
    url = f"https://www.bing.com/search?q={quote(query)}&format=rss"
    try:
      with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": _USER_AGENT})
    except httpx.HTTPError as exc:
      return {"error": f"Web search failed: {exc}"}

    if response.status_code != 200:
      return {"error": f"Web search failed with HTTP {response.status_code}."}

    results = parse_rss_items(response.text, limit=arguments["max_results"])
    if not results:
      return {"results": [], "note": "No results found."}
    return {"query": query, "count": len(results), "results": results}


class _FetchPageInput(BaseModel):
  url: str = Field(description="http(s) URL of the page to read.")
  max_chars: int = Field(
    default=8_000,
    ge=500,
    le=_MAX_FETCH_CHARS,
    description="Max characters of extracted text to return.",
  )


class FetchPageTool(Tool):
  name = "fetch_page"
  description = (
    "Fetch a web page and return its readable text (markup stripped). "
    "Use with URLs from web_search to answer from the actual content."
  )
  input_model = _FetchPageInput

  def execute(self, arguments: dict[str, Any]) -> Any:
    url = arguments["url"].strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
      return {"error": "Only http(s) URLs are allowed."}

    try:
      with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True, max_redirects=5) as client:
        response = client.get(url, headers={"User-Agent": _USER_AGENT})
    except httpx.HTTPError as exc:
      return {"error": f"Failed to fetch page: {exc}"}

    if response.status_code != 200:
      return {"error": f"Failed to fetch page with HTTP {response.status_code}."}

    text = extract_html_text(response.text)
    max_chars = arguments["max_chars"]
    truncated = len(text) > max_chars
    return {
      "url": str(response.url),
      "status": response.status_code,
      "text": text[:max_chars],
      "truncated": truncated,
    }


def build_web_tools() -> list[Tool]:
  return [WebSearchTool(), FetchPageTool()]