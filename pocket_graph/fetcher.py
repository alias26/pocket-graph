"""
Fetch external content (URLs, search results) into the raw/ corpus
so the graph can ingest it.

Two modes:
  fetch_url(url, raw_dir)        -- single URL -> file in raw/
                                   (auto-detects arxiv -> annotated markdown,
                                    otherwise saves the bytes as-is)
  search_and_fetch(query, ...)   -- web search -> top results -> raw/

The search mode is best invoked from inside Claude Code (which has its own
web_search tool). The CLI fallback uses urllib + a generic Google/DuckDuckGo
HTML scraper, which is brittle but works without an API key.
"""
from __future__ import annotations
import hashlib
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# URL classification
# ============================================================

_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")


def _detect_url_type(url: str) -> str:
    """Classify URL for targeted fetching."""
    lower = url.lower()
    if "arxiv.org" in lower:
        return "arxiv"
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    return "webpage"


# ============================================================
# Helpers
# ============================================================


def _yaml_str(s: str) -> str:
    """Escape a string for embedding in a YAML double-quoted scalar."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def _safe_filename(url: str, max_len: int = 80) -> str:
    """Derive a safe filename from a URL."""
    # Strip query/fragment, take last path segment
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else parsed.netloc

    # Sanitize
    name = re.sub(r"[^\w.\-]+", "_", name)[:max_len]
    if not name:
        name = "page"

    # Ensure unique by appending hash prefix
    h = hashlib.sha256(url.encode()).hexdigest()[:8]

    # Ensure it has a sensible extension
    if "." not in name:
        # Guess from path
        if path.endswith((".pdf", ".html", ".txt", ".md")):
            pass  # already has ext (caught earlier)
        else:
            name = name + ".html"

    base, ext = name.rsplit(".", 1) if "." in name else (name, "html")
    return f"{base}-{h}.{ext}"


def _fetch_arxiv(url: str, raw_dir: Path,
                  user_agent: str = "Mozilla/5.0 (compatible; pocket-graph/0.1)") -> Path:
    """Fetch an arxiv URL: metadata markdown AND the full PDF.

    arxiv abs URLs only have metadata + abstract -- not the paper body. To get
    the actual paper for graph extraction, we also download the PDF and save
    it alongside the metadata markdown.

    Saves:
      - arxiv_<id>.md   (frontmatter + title/authors/abstract)
      - arxiv_<id>.pdf  (full paper body -- used by tree-sitter / PyMuPDF)

    Returns the PDF path (the body file). The .md is sidecar metadata.
    Falls back to plain webpage fetch if extraction fails.
    """
    m = _ARXIV_ID_RE.search(url)
    if not m:
        # No arxiv ID in URL -- fall back to webpage fetch
        return _fetch_generic(url, raw_dir, user_agent)

    arxiv_id = m.group(1)
    api_url = f"https://export.arxiv.org/abs/{arxiv_id}"
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    title, abstract, authors = arxiv_id, "", ""
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        title_match = re.search(r'class="title[^"]*"[^>]*>(.*?)</h1>',
                                 html, re.DOTALL | re.IGNORECASE)
        if title_match:
            title = re.sub(r"<[^>]+>", " ", title_match.group(1)).strip()
            title = re.sub(r"^Title:\s*", "", title, flags=re.IGNORECASE).strip()

        authors_match = re.search(r'class="authors"[^>]*>(.*?)</div>',
                                   html, re.DOTALL | re.IGNORECASE)
        if authors_match:
            authors = re.sub(r"<[^>]+>", "", authors_match.group(1)).strip()
            authors = re.sub(r"^Authors?:\s*", "", authors, flags=re.IGNORECASE).strip()

        abstract_match = re.search(
            r'class="abstract[^"]*"[^>]*>(.*?)</blockquote>',
            html, re.DOTALL | re.IGNORECASE)
        if abstract_match:
            abstract = re.sub(r"<[^>]+>", "", abstract_match.group(1)).strip()
            abstract = re.sub(r"^Abstract:\s*", "", abstract, flags=re.IGNORECASE).strip()
            # Collapse whitespace
            abstract = re.sub(r"\s+", " ", abstract).strip()
    except Exception as e:
        print(f"[pocket_graph.fetch] arxiv API failed ({e}); saving stub.")

    now = datetime.now(timezone.utc).isoformat()
    content = f"""---
source_url: "{_yaml_str(url)}"
arxiv_id: "{_yaml_str(arxiv_id)}"
type: paper
title: "{_yaml_str(title)}"
paper_authors: "{_yaml_str(authors)}"
captured_at: {now}
---

# {title}

**Authors:** {authors}
**arXiv:** {arxiv_id}
**Source:** {url}

## Abstract

{abstract}
"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    md_dest = raw_dir / f"arxiv_{arxiv_id.replace('.', '_')}.md"
    md_dest.write_text(content, encoding="utf-8")
    print(f"[pocket_graph.fetch] saved {md_dest} (arxiv {arxiv_id} metadata)")

    # Also fetch the PDF body so the graph can extract the paper itself.
    pdf_dest = raw_dir / f"arxiv_{arxiv_id.replace('.', '_')}.pdf"
    if pdf_dest.exists():
        print(f"[pocket_graph.fetch] PDF already cached: {pdf_dest}")
        return pdf_dest

    try:
        req = urllib.request.Request(pdf_url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        pdf_dest.write_bytes(data)
        print(f"[pocket_graph.fetch] saved {pdf_dest} ({len(data)} bytes -- paper body)")
        return pdf_dest
    except Exception as e:
        print(f"[pocket_graph.fetch] WARNING: PDF fetch failed ({e})")
        print(f"[pocket_graph.fetch]          metadata-only at {md_dest}")
        print(f"[pocket_graph.fetch]          download manually: {pdf_url}")
        return md_dest


def _fetch_generic(url: str, raw_dir: Path, user_agent: str) -> Path:
    """Generic URL fetch -- saves bytes as-is, no metadata extraction."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(url)
    dest = raw_dir / filename

    if dest.exists():
        print(f"[pocket_graph.fetch] already cached: {dest}")
        return dest

    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
            data = resp.read()
    except Exception as e:
        raise RuntimeError(f"fetch failed for {url}: {e}") from e

    ext_map = {
        "application/pdf": ".pdf",
        "text/html": ".html",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "application/json": ".json",
    }
    correct_ext = ext_map.get(content_type)
    if correct_ext and not dest.name.endswith(correct_ext):
        dest = dest.with_suffix(correct_ext)

    dest.write_bytes(data)
    print(f"[pocket_graph.fetch] saved {dest} ({len(data)} bytes, {content_type})")
    return dest


def fetch_url(url: str, raw_dir: Path,
               user_agent: str = "Mozilla/5.0 (compatible; pocket-graph/0.1)") -> Path:
    """Fetch a URL into raw/ -- auto-detects type and uses the best fetcher.

    URL types:
      - arxiv (arxiv.org/...): saves an annotated markdown with title/authors/abstract
      - everything else: saves the raw bytes (PDF, HTML, text, ...)
    """
    url_type = _detect_url_type(url)
    if url_type == "arxiv":
        return _fetch_arxiv(url, raw_dir, user_agent)
    return _fetch_generic(url, raw_dir, user_agent)


def search_and_fetch(query: str, raw_dir: Path,
                      max_results: int = 3) -> list[Path]:
    """Search the web for `query`, fetch top results, save to raw_dir/.

    Returns list of saved file paths.

    NOTE: This uses DuckDuckGo's HTML scrape which is brittle. For real
    use inside Claude Code, prefer the agent's own web_search tool and
    feed URLs to fetch_url() directly.
    """
    # DuckDuckGo HTML endpoint -- no API key needed
    search_url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    try:
        req = urllib.request.Request(
            search_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; pocket-graph/0.1)"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        raise RuntimeError(f"search failed: {e}") from e

    # DuckDuckGo result link pattern: <a class="result__a" href="...">
    urls = re.findall(r'class="result__a"[^>]+href="([^"]+)"', html)
    # DuckDuckGo wraps with redirect: //duckduckgo.com/l/?uddg=ENCODED_URL
    cleaned = []
    for u in urls:
        m = re.search(r"uddg=([^&]+)", u)
        if m:
            cleaned.append(urllib.parse.unquote(m.group(1)))
        elif u.startswith("http"):
            cleaned.append(u)
    cleaned = cleaned[:max_results]

    if not cleaned:
        raise RuntimeError(
            f"No results for {query!r}. DuckDuckGo HTML format may have changed; "
            "use Claude Code's web_search tool to find URLs and pass them to "
            "`pocket-graph ingest <url>` directly."
        )

    saved = []
    for u in cleaned:
        try:
            saved.append(fetch_url(u, raw_dir))
        except Exception as e:
            print(f"[pocket_graph.fetch] skipped {u}: {e}")
    return saved


__all__ = ["fetch_url", "search_and_fetch"]
