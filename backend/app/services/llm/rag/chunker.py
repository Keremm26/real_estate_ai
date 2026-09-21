"""
Structure-aware chunking of Italian regulatory text.

Legal rules are self-contained per article, so we split on article markers
("Art. 24", "Articolo 24-bis", ...) rather than by blind character count —
each chunk is one article, retrieved whole. This is what makes threshold
extraction reliable: the minimum-room-area rule comes back as one intact unit.

If no article markers are found (e.g. a municipal annex table), we fall back
to a size-based split so nothing is silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# "Art. 24", "Articolo 24", "Art. 23-bis", "Art. 24 ter" ...
_ORDINAL = r"(?:bis|ter|quater|quinquies|sexies|septies|octies|novies|decies)"
_ARTICLE_RE = re.compile(
    rf"(?im)^\s*(art(?:icolo)?\.?\s*\d+(?:[\-\s]{_ORDINAL})?)\b",
)

# a number with a unit (either order — IT writes both "2,70 m" and "metri 2,70"),
# or a fraction like "1/8" -> signals a quantitative rule
# "Does this chunk carry a numeric threshold?" — drives the has_quantitative
# metadata flag that the regulatory extractor routes on. Unit spellings cover
# IT / EN / ES (m², mq, sqm, square metres, metros cuadrados, ...). The fraction
# alternative catches ratios like the DM Sanità 1/8 aero-illumination rule but
# excludes year-shaped denominators, otherwise every "Ley 9/2001" / "RD 314/2006"
# citation would be flagged.
_UNITS = r"m²|m2|mq|sqm|sq\.?\s*m\b|square\s+met(?:re|er)s?|metros?\s+cuadrados?|metri(?:\s+quadr[io])?|cm"
_QUANT_RE = re.compile(
    rf"\d+(?:[.,]\d+)?\)?\s*(?:{_UNITS}|m\.?\b|%|per\s*cent[o]?\b)"  # number then unit ("(15) per cento")
    rf"|(?:{_UNITS})\s*\d+(?:[.,]\d+)?"                        # worded unit then number
    r"|\bm\.?\s*\d+(?:[.,]\d+)?"                               # bare 'm 2,70' / 'm2'
    r"|(?<!\d)\d{1,2}\s*/\s*\d{1,3}(?!\d)",                    # ratio (1/8), not 9/2001
    re.IGNORECASE,
)

_FALLBACK_MAX_CHARS = 2000

# A numbered section header inside an annex/table that has no "Art." markers:
# "6.1.2. Title", "5. Finalità". The number and title must be on the SAME line
# (so a stray PDF page-number line isn't mistaken for a header), and the title
# must start with a capital letter (so list items like "1. ad albergo" and data
# rows like "18 mq ..." are excluded).
_SECTION_RE = re.compile(r"(?m)^[ \t]*(\d+(?:\.\d+){0,3})[.)][ \t]+(\S.*)$")


@dataclass
class ArticleChunk:
    article_ref: str          # normalised, e.g. "Art. 24"
    text: str                 # header + body of the article
    has_quantitative: bool


def _normalise_ref(raw: str) -> str:
    """'Articolo  24-bis' / 'art.24' -> 'Art. 24-bis'."""
    m = re.search(rf"(\d+)(?:[\-\s]({_ORDINAL}))?", raw, re.IGNORECASE)
    if not m:
        return raw.strip()
    num = m.group(1)
    ordinal = m.group(2)
    return f"Art. {num}-{ordinal.lower()}" if ordinal else f"Art. {num}"


def _looks_like_heading(title: str) -> bool:
    """A numbered line is a section heading (not a list item or data row) when
    its title's first letter is capitalised."""
    for ch in title:
        if ch.isalpha():
            return ch.isupper()
    return False


def _fallback_split(text: str) -> List[ArticleChunk]:
    """No 'Art.' markers. Prefer the document's own numbered-section structure
    (e.g. a PDF annex: '6.1.2. Area Funzionale ...') so each rule becomes its own
    chunk; fall back to size-capped line packing only when there is no such
    structure, so nothing is dropped or collapsed into one blob."""
    headers = [m for m in _SECTION_RE.finditer(text) if _looks_like_heading(m.group(2))]
    if not headers:
        return _size_pack(text)
    chunks: List[ArticleChunk] = []
    for i, m in enumerate(headers):
        start = m.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[start:end].strip()
        if body:
            chunks.append(ArticleChunk(f"§ {m.group(1)}", body, bool(_QUANT_RE.search(body))))
    return chunks


def _size_pack(text: str) -> List[ArticleChunk]:
    """Last resort for text with no article and no numbered-section structure:
    pack lines into size-capped sections so nothing is dropped or left as one blob."""
    chunks: List[ArticleChunk] = []
    buf, size, idx = [], 0, 1
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if size + len(line) > _FALLBACK_MAX_CHARS and buf:
            body = "\n".join(buf)
            chunks.append(ArticleChunk(f"Section {idx}", body, bool(_QUANT_RE.search(body))))
            buf, size, idx = [], 0, idx + 1
        buf.append(line)
        size += len(line) + 1
    if buf:
        body = "\n".join(buf)
        chunks.append(ArticleChunk(f"Section {idx}", body, bool(_QUANT_RE.search(body))))
    return chunks


def chunk_document(text: str) -> List[ArticleChunk]:
    """Split one document's cleaned text into article-level chunks."""
    matches = list(_ARTICLE_RE.finditer(text))
    if not matches:
        return _fallback_split(text)

    # A ref recurs when a table of contents repeats the heading before the body,
    # when a PDF running header repeats it on every page, or when numbering
    # restarts per Titolo. Two chunks with the same article_ref would collide on
    # chunk_id at upsert time, so merge every piece per ref in document order —
    # lossless, unlike keeping only the longest piece (which drops the rest).
    merged: dict[str, List[str]] = {}
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            merged.setdefault(_normalise_ref(m.group(1)), []).append(body)
    return [
        ArticleChunk(
            article_ref=ref,
            text=body,
            has_quantitative=bool(_QUANT_RE.search(body)),
        )
        for ref, pieces in merged.items()
        for body in ("\n".join(pieces),)
    ]
