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

# legislation.gov.uk decorates every heading with a territorial-extent marker
# (<span class="LegExtentRestriction">E+W</span>). It is not legal content and,
# because the markup carries no whitespace, it gets glued onto the heading text
# ("Citation and commencementE+W1."), corrupting the section reference.
_STRIP_CLASSES = ("LegExtentRestriction",)

# Block-level elements must not run together when their markup has no
# whitespace between them; give each one a trailing newline before extraction.
_BLOCK_TAGS = (
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "div", "li", "tr", "br", "section", "article",
)


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

    # Detect legislation.gov.uk markup *before* stripping it. Only that source
    # needs block separation: its elements carry no whitespace, so headings and
    # provision numbers run together. Applying it everywhere would push inline
    # cross-references ("...art. 18 della legge n. 765") to line starts and split
    # real articles on a citation, so it stays scoped to this markup.
    is_legislation = bool(
        tree.find_class("LegExtentRestriction") or tree.find_class("LegP1GroupTitle")
    )
    for cls in _STRIP_CLASSES:
        for el in tree.find_class(cls):
            el.drop_tree()
    if is_legislation:
        for el in tree.iter(*_BLOCK_TAGS):
            el.tail = (el.tail or "") + "\n"

    text = tree.text_content()
    # normalise whitespace but keep line structure (article splitting needs newlines)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _clean_pdf(raw: bytes) -> str:
    """Extract text from a PDF. Some regulatory annexes (e.g. dimensional
    standards) are published PDF-only, so we read them page by page and
    normalise whitespace the same way ``_clean_html`` does."""
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(raw))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


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
    txt_path = cache_dir / f"{slug}.txt"

    if txt_path.exists() and not force:
        return txt_path.read_text(encoding="utf-8")

    # raw cache extension follows the source (.pdf vs .html); cleaning is decided
    # by content sniffing below, so a mislabelled URL still parses correctly.
    is_pdf_url = url.split("?")[0].lower().endswith(".pdf")
    raw_path = cache_dir / f"{slug}.{'pdf' if is_pdf_url else 'html'}"

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
        raw = resp.content  # bytes — let lxml/pypdf detect the charset/format
        raw_path.write_bytes(raw)

    cleaned = _clean_pdf(raw) if raw[:5] == b"%PDF-" else _clean_html(raw)
    txt_path.write_text(cleaned, encoding="utf-8")
    return cleaned
