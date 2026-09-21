"""
LLM-assisted structure detection for chunking (optional strategy).

Motivation: a per-country regex zoo (IT ``Art.``, ES ``Artículo`` + CTE ``DB-SI``
sections, EN ``Section``/``Regulation``/``Policy H15``/Approved-Document numbering,
...) does not scale to many jurisdictions, and the deterministic fallback can
silently corrupt (duplicate section refs -> chunk_id collisions).

Approach: a local model inspects only a *sample* of the document and returns a
**boundary regex** — where each self-contained unit (article/section) begins. We
then slice the full document deterministically at those offsets. The model never
returns the legal text, so nothing is paraphrased, dropped, or invented; the
segmentation stays lossless and citable. The detected regex is cached per
document, so the model runs once and re-ingestion is reproducible.

If detection fails or looks wrong, we fall back to the deterministic chunker.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, Optional

import httpx

from app.services.llm.rag import config
from app.services.llm.rag.chunker import (
    ArticleChunk,
    _normalise_ref,
    _QUANT_RE,
    chunk_document,
)
from app.services.llm.rag.fetch import _slug

# JSON schema the model must fill (Ollama structured output).
_SCHEMA = {
    "type": "object",
    "properties": {
        "marker_regex": {"type": "string"},
        "example_headers": {"type": "array", "items": {"type": "string"}},
        "unit_name": {"type": "string"},
    },
    "required": ["marker_regex", "example_headers"],
}

_SYSTEM = """You analyse the beginning of a legal/regulatory document and work out how it is divided into its smallest self-contained normative units (e.g. articles, sections, regulations, annex clauses).

Return ONLY a JSON object with:
- "marker_regex": a Python `re` regex (used with re.MULTILINE) that matches the START LINE of each unit, with exactly ONE capturing group around the unit's short identifier (e.g. "Art. 5", "Artículo 12", "Section 3", "6.1.2"). Anchor to line start with ^. Match only real unit headers — NOT every numbered line, list item, table row, or cross-reference.
- "example_headers": 2-4 exact header strings copied from the text that your regex must match.
- "unit_name": what the units are called (e.g. "articolo", "artículo", "section", "regulation").

Guidance:
- Prefer the coarsest real unit (a whole article/section), not sub-paragraphs.
- The regex must match the headers as they literally appear at the start of a line.
- Keep the regex simple and linear (avoid nested quantifiers) to stay fast.
- Escape backslashes correctly for JSON (e.g. \\\\d for a digit)."""


def _loads_lenient(s: str) -> Optional[dict]:
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _openai_key() -> Optional[str]:
    """API key from the environment, falling back to the app settings (.env)."""
    key = os.getenv("OPENAI_API_KEY")
    if key:
        return key
    try:
        from app.core.config import settings

        return getattr(settings, "OPENAI_API_KEY", None)
    except Exception:
        return None


def _detect_openai(sample: str) -> Optional[str]:
    """Hosted detector. Deliberately uses the real OpenAI base, not the
    institutional vLLM base that ``OPENAI_API_BASE`` points at for gemma."""
    key = _openai_key()
    if not key:
        return None
    payload = {
        "model": config.CHUNK_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Document sample:\n\n{sample}"},
        ],
        "response_format": {"type": "json_object"},
    }
    r = httpx.post(
        f"{config.CHUNK_OPENAI_BASE.rstrip('/')}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {key}"},
        timeout=config.CHUNK_DETECT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _detect_ollama(sample: str) -> Optional[str]:
    """Local detector (structured output via Ollama's JSON-schema `format`)."""
    payload = {
        "model": config.CHUNK_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Document sample:\n\n{sample}"},
        ],
        "stream": False,
        "format": _SCHEMA,
        "options": {"temperature": 0},
    }
    r = httpx.post(
        f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/chat",
        json=payload,
        timeout=config.CHUNK_DETECT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


def detect_structure(sample: str) -> Optional[dict]:
    """Ask the configured model for a boundary regex for this document's sample."""
    backend = (config.CHUNK_BACKEND or "openai").lower()
    try:
        content = _detect_openai(sample) if backend == "openai" else _detect_ollama(sample)
    except Exception:
        return None
    if not content:
        return None
    spec = _loads_lenient(content)
    if not spec or not spec.get("marker_regex"):
        return None
    return spec


def _compile(pattern: str):
    for flags in (re.MULTILINE, re.MULTILINE | re.IGNORECASE):
        try:
            return re.compile(pattern, flags)
        except re.error:
            continue
    return None


# An Italian article header the model may capture as "Art. 1." / "Articolo 24-bis".
# Normalised to the SAME canonical form the deterministic chunker emits, so refs
# stay citable and the gold-set labels keep matching across both strategies.
_IT_ART = re.compile(r"^art(?:icolo)?\.?\s*\d+", re.IGNORECASE)


def _normalise_llm_ref(raw: str, index: int) -> str:
    """Canonicalise a model-captured reference. Italian articles are forced to
    'Art. N' / 'Art. N-bis'; other refs pass through cleaned but intact
    (e.g. 'Artículo 5', 'Disposición transitoria primera')."""
    ref = re.sub(r"\s+", " ", raw or "").strip().strip(" .:;—-")
    if not ref:
        return f"Unit {index}"
    if _IT_ART.match(ref):
        return _normalise_ref(ref)
    return ref[:60]


def _slice(text: str, rx) -> List[ArticleChunk]:
    """Slice the full text at header offsets, merging every piece that carries
    the same ref (in document order) into one chunk.

    A ref legitimately recurs in three ways: a table of contents repeats the
    heading before the body; a PDF running header repeats it on every page of a
    long article (NUEA Torino 'Art. 8' x41); and some regulations restart the
    numbering per Titolo (Regolamento d'Igiene). Keeping only the longest piece
    — the previous behaviour — silently dropped the rest (47% of CTE DB-SUA,
    44% of the NUEA). Merging is lossless; ``_cap`` then sub-splits anything
    oversized into '(part N)' as it already does for long single articles."""
    matches = list(rx.finditer(text))
    if len(matches) < 2:
        return []
    merged: dict[str, List[str]] = {}
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        ref = _normalise_llm_ref(m.group(1) if m.groups() else m.group(0), i + 1)
        merged.setdefault(ref, []).append(body)
    return [
        ArticleChunk(ref, body, bool(_QUANT_RE.search(body)))
        for ref, pieces in merged.items()
        for body in ("\n".join(pieces),)
    ]


def _pack_lines(text: str, max_chars: int) -> List[str]:
    """Split text into <=max_chars parts on line boundaries (never mid-line)."""
    parts, buf, size = [], [], 0
    for line in text.splitlines():
        if size + len(line) > max_chars and buf:
            parts.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        parts.append("\n".join(buf))
    return parts


def _cap(chunks: List[ArticleChunk], max_chars: int) -> List[ArticleChunk]:
    """Sub-split oversized chunks so one huge section cannot swamp the top-k
    budget. The semantic ref is kept and the parts are numbered."""
    out: List[ArticleChunk] = []
    for c in chunks:
        if len(c.text) <= max_chars:
            out.append(c)
            continue
        parts = _pack_lines(c.text, max_chars)
        for i, body in enumerate(parts, 1):
            ref = c.article_ref if len(parts) == 1 else f"{c.article_ref} (part {i})"
            out.append(ArticleChunk(ref, body, bool(_QUANT_RE.search(body))))
    return out


def _spec_cache_path(cache_key: str) -> Path:
    return config.RAW_CACHE_DIR / f"{_slug(cache_key)}.chunkspec.json"


def chunk_document_llm(
    text: str,
    *,
    cache_key: Optional[str] = None,
    force_detect: bool = False,
) -> List[ArticleChunk]:
    """LLM-detected boundaries -> deterministic slice, with deterministic
    fallback. ``cache_key`` (e.g. the doc_key) caches the detected regex so the
    model runs once and re-ingestion is reproducible."""
    spec = None
    cache_path = _spec_cache_path(cache_key) if cache_key else None
    if cache_path and cache_path.exists() and not force_detect:
        spec = _loads_lenient(cache_path.read_text(encoding="utf-8"))

    if spec is None:
        spec = detect_structure(text[: config.CHUNK_SAMPLE_CHARS])
        if spec and cache_path:
            cache_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    if spec:
        rx = _compile(spec["marker_regex"])
        if rx:
            chunks = _slice(text, rx)
            # sanity: enough units, and not absurd over-fragmentation
            if 2 <= len(chunks) <= max(4, len(text) // 200):
                return _cap(chunks, config.CHUNK_MAX_CHARS)

    # detection failed or looked wrong -> deterministic splitter
    return chunk_document(text)
