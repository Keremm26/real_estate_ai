"""
Fetch + clean regulatory source texts (ingestion-time only).

Italian legal texts are public domain (L.633/1941 art. 5), fetched once at
ingestion and cached locally — retrieval never hits the network.

Cache-first by design: if a cached raw file exists it is reused, and you can
drop a manually-saved HTML file into the raw cache when a site blocks
automated fetches. The public entry point is ``fetch_text``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import httpx
from lxml import html as lxml_html

from app.services.llm.rag import config

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# tags whose text is never legal content
_STRIP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "form")


def _slug(doc_key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", doc_key.lower()).strip("_")


def _clean_html(raw: bytes) -> str:
    """Extract readable text from an HTML page, dropping chrome/boilerplate.

    Parses raw *bytes* so lxml can honour the page's declared charset — many
    Italian legal mirrors are Windows-1252/Latin-1, and decoding them as UTF-8
    mangles the accented characters.
    """
    tree = lxml_html.fromstring(raw)
    for el in tree.iter(*_STRIP_TAGS):
        el.drop_tree()
    text = tree.text_content()
    # normalise whitespace but keep line structure (article splitting needs newlines)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def fetch_text(
    doc_key: str,
    url: str,
    *,
    force: bool = False,
    cache_dir: Optional[Path] = None,
) -> str:
    """
    Return cleaned text for a document, caching both raw and cleaned forms.

    ``doc_key`` is a stable name (e.g. "dpr_380_2001") used for cache filenames.
    Set ``force=True`` to re-fetch even if cached.
    """
    cache_dir = cache_dir or config.RAW_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(doc_key)
    raw_path = cache_dir / f"{slug}.html"
    txt_path = cache_dir / f"{slug}.txt"

    if txt_path.exists() and not force:
        return txt_path.read_text(encoding="utf-8")

    if raw_path.exists() and not force:
        raw = raw_path.read_bytes()
    else:
        resp = httpx.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "it,en;q=0.8"},
            follow_redirects=True,
            timeout=60.0,
        )
        resp.raise_for_status()
        raw = resp.content  # bytes — let lxml detect the charset
        raw_path.write_bytes(raw)

    cleaned = _clean_html(raw)
    txt_path.write_text(cleaned, encoding="utf-8")
    return cleaned
