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
_QUANT_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:m²|mq|m\.?\b|cm|metri(?:\s+quadri)?|%)"   # number then unit
    r"|(?:m²|mq|metri(?:\s+quadri)?|cm)\s*\d+(?:[.,]\d+)?"          # worded unit then number
    r"|\bm\.?\s*\d+(?:[.,]\d+)?"                                     # bare 'm 2,70' / 'm2'
    r"|(?<!\d)\d+\s*/\s*\d+",                                        # fraction (e.g. 1/8)
    re.IGNORECASE,
)

_FALLBACK_MAX_CHARS = 2000


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


def _fallback_split(text: str) -> List[ArticleChunk]:
    """No article markers: split on blank-line paragraphs, packed to a size cap."""
    chunks: List[ArticleChunk] = []
    buf, size, idx = [], 0, 1
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if size + len(para) > _FALLBACK_MAX_CHARS and buf:
            body = "\n".join(buf)
            chunks.append(ArticleChunk(f"Section {idx}", body, bool(_QUANT_RE.search(body))))
            buf, size, idx = [], 0, idx + 1
        buf.append(para)
        size += len(para)
    if buf:
        body = "\n".join(buf)
        chunks.append(ArticleChunk(f"Section {idx}", body, bool(_QUANT_RE.search(body))))
    return chunks


def chunk_document(text: str) -> List[ArticleChunk]:
    """Split one document's cleaned text into article-level chunks."""
    matches = list(_ARTICLE_RE.finditer(text))
    if not matches:
        return _fallback_split(text)

    chunks: List[ArticleChunk] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        chunks.append(
            ArticleChunk(
                article_ref=_normalise_ref(m.group(1)),
                text=body,
                has_quantitative=bool(_QUANT_RE.search(body)),
            )
        )

    # Mirror pages often repeat every article in a table-of-contents (title only)
    # before the real body, producing two chunks per article that collide on the
    # same chunk_id at upsert time. Collapse to the longest chunk per article_ref
    # so the substantive body always wins, independent of document order.
    best: dict[str, ArticleChunk] = {}
    for c in chunks:
        if c.article_ref not in best or len(c.text) > len(best[c.article_ref].text):
            best[c.article_ref] = c
    return list(best.values())
