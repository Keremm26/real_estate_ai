"""
Retrieval — the online path that replaces ``load_regulatory_documents()``.

Three modes, selected by ``config.RETRIEVAL_MODE`` (feature flag for the
three-arm downstream evaluation):

  none : no retrieval at all — the caller falls back to the legacy folder
         loader (which is empty in production). ``search`` returns ``[]``.
  dump : same jurisdiction scope as ``rag`` but NO ranking — return every
         in-scope chunk. Reproduces the old "dump everything" strategy on the
         new corpus. Scope is held constant across dump/rag so the A/B
         isolates the semantic-retrieval delta.
  rag  : router -> jurisdiction cascade (metadata ``where``) -> semantic top-k

Returns ``(docs_str, sources)`` in the same block shape the extraction prompt
already expects, so the downstream prompt and ``_run_ranking`` are unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.llm.rag import config, store
from app.services.llm.rag.embedder import embed_query

_EMPTY = ("No regulatory documents available.", [])

# Metadata keys surfaced in the per-hit trace (kept small: the trace is stored
# in the run's gemini_responses / agent_trace, not the chunk text itself).
_TRACE_KEYS = ("doc_name", "article_ref", "jurisdiction_level", "use_case", "doc_type", "source_url")


def _cascade_where(country: str, use_case: str, *, quantitative_only: bool = False) -> Dict[str, Any]:
    """Jurisdiction cascade filter: this country, plus general baselines.
    ``quantitative_only`` narrows to chunks carrying a numeric threshold."""
    use_cases = list(dict.fromkeys([use_case, "general"]))  # dedupe, keep order
    clauses: List[Dict[str, Any]] = [
        {"country": country},
        {"use_case": {"$in": use_cases}},
    ]
    if quantitative_only:
        clauses.append({"has_quantitative": True})
    return {"$and": clauses}


def search(
    query: str,
    *,
    country: str = config.DEFAULT_COUNTRY,
    use_case: str = config.DEFAULT_USE_CASE,
    top_k: int = config.DEFAULT_TOP_K,
    mode: Optional[str] = None,
    frame: bool = False,
    quantitative_only: bool = False,
) -> List[Dict[str, Any]]:
    """Structured retrieval: return ranked hits (metadata + text), not formatted text.

    Each hit is the chunk's metadata dict plus ``text`` and, in ``rag`` mode,
    ``distance`` (cosine). ``dump`` returns every in-scope chunk unranked. This
    is the low-level primitive used by both ``retrieve()`` (which formats it for
    the prompt) and the retrieval evaluation (which scores the ranking).

    ``frame`` / ``quantitative_only`` are the extraction-oriented knobs (see
    config.QUERY_FRAMING / QUANT_ONLY); they only affect ``rag`` mode — ``dump``
    stays the pure "everything in scope" baseline.
    """
    mode = (mode or config.RETRIEVAL_MODE).lower()
    if mode not in config.RETRIEVAL_MODES:
        raise ValueError(f"Unknown retrieval mode {mode!r}; expected one of {config.RETRIEVAL_MODES}")
    if mode == "none":
        return []

    col = store.get_collection()
    if col.count() == 0:
        return []

    if mode == "dump":
        # baseline: every in-scope chunk, no ranking
        res = col.get(where=_cascade_where(country, use_case))
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        return [{**md, "text": doc, "distance": None} for doc, md in zip(docs, metas)]

    # rag: semantic top-k within the cascade scope
    where = _cascade_where(country, use_case, quantitative_only=quantitative_only)
    embed_text = f"{query}\n{config.QUERY_FRAMING_TEXT}" if frame else query
    qvec = embed_query(embed_text)
    res = col.query(query_embeddings=[qvec], n_results=top_k, where=where)
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0] or [None] * len(docs)
    return [{**md, "text": doc, "distance": dist} for doc, md, dist in zip(docs, metas, dists)]


def _format(hits: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    blocks: List[str] = []
    sources: List[str] = []
    for hit in hits:
        doc = hit.get("doc_name", "?")
        art = hit.get("article_ref", "")
        tier = hit.get("jurisdiction_level", "")
        blocks.append(f"--- {doc} {art} ({tier}) ---\n{hit.get('text', '')}\n")
        sources.append(f"{doc} {art} ({hit.get('source_url', '')})")
    return "\n".join(blocks), sources


def retrieve(
    query: str,
    *,
    country: str = config.DEFAULT_COUNTRY,
    use_case: str = config.DEFAULT_USE_CASE,
    top_k: int = config.DEFAULT_TOP_K,
    mode: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """Retrieve regulatory context for a query, scoped by jurisdiction + use case.

    Returns ``(docs_str, sources)`` in the block shape the extraction prompt
    expects, so downstream code is unchanged.
    """
    docs_str, sources, _ = retrieve_with_trace(
        query, country=country, use_case=use_case, top_k=top_k, mode=mode
    )
    return docs_str, sources


def retrieve_with_trace(
    query: str,
    *,
    country: str = config.DEFAULT_COUNTRY,
    use_case: str = config.DEFAULT_USE_CASE,
    top_k: int = config.DEFAULT_TOP_K,
    mode: Optional[str] = None,
    frame: bool = False,
    quantitative_only: bool = False,
) -> Tuple[str, List[str], Dict[str, Any]]:
    """``retrieve()`` plus a JSON-serialisable trace of what was retrieved.

    The trace is what the downstream evaluation scores against (citation
    accuracy = did the LLM cite a document that was actually in its context?)
    and what the UI shows under the regulatory step. Shape::

        {"mode", "country", "use_case", "top_k", "framed", "quantitative_only",
         "n_chunks", "context_chars",
         "hits": [{"doc_name", "article_ref", "jurisdiction_level", ..., "distance"}]}
    """
    mode = (mode or config.RETRIEVAL_MODE).lower()
    hits = search(query, country=country, use_case=use_case, top_k=top_k, mode=mode,
                  frame=frame, quantitative_only=quantitative_only)
    docs_str, sources = _format(hits) if hits else _EMPTY
    trace = {
        "mode": mode,
        "country": country,
        "use_case": use_case,
        "top_k": top_k if mode == "rag" else None,
        "framed": bool(frame) if mode == "rag" else False,
        "quantitative_only": bool(quantitative_only) if mode == "rag" else False,
        "n_chunks": len(hits),
        "context_chars": len(docs_str) if hits else 0,
        "hits": [
            {**{k: h.get(k) for k in _TRACE_KEYS}, "distance": h.get("distance")}
            for h in hits
        ],
    }
    return docs_str, sources, trace
