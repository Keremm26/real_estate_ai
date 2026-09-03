"""
Retrieval — the online path that replaces ``load_regulatory_documents()``.

Two modes, selected by ``config.RETRIEVAL_MODE`` (feature flag for the
before/after evaluation):

  rag  : router -> jurisdiction cascade (metadata ``where``) -> semantic top-k
  dump : same jurisdiction scope but NO ranking — return every in-scope chunk.
         Reproduces the old "dump everything" baseline. Scope is held constant
         across both arms so the A/B isolates the semantic-retrieval delta.

Returns ``(docs_str, sources)`` in the same block shape the extraction prompt
already expects, so the downstream prompt and ``_run_ranking`` are unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.llm.rag import config, store
from app.services.llm.rag.embedder import embed_query

_EMPTY = ("No regulatory documents available.", [])


def _cascade_where(country: str, use_case: str) -> Dict[str, Any]:
    """Jurisdiction cascade filter: this country, plus general baselines."""
    use_cases = list(dict.fromkeys([use_case, "general"]))  # dedupe, keep order
    return {
        "$and": [
            {"country": country},
            {"use_case": {"$in": use_cases}},
        ]
    }


def search(
    query: str,
    *,
    country: str = config.DEFAULT_COUNTRY,
    use_case: str = config.DEFAULT_USE_CASE,
    top_k: int = config.DEFAULT_TOP_K,
    mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Structured retrieval: return ranked hits (metadata + text), not formatted text.

    Each hit is the chunk's metadata dict plus ``text`` and, in ``rag`` mode,
    ``distance`` (cosine). ``dump`` returns every in-scope chunk unranked. This
    is the low-level primitive used by both ``retrieve()`` (which formats it for
    the prompt) and the retrieval evaluation (which scores the ranking).
    """
    mode = (mode or config.RETRIEVAL_MODE).lower()
    col = store.get_collection()
    if col.count() == 0:
        return []

    where = _cascade_where(country, use_case)

    if mode == "dump":
        # baseline: every in-scope chunk, no ranking
        res = col.get(where=where)
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        return [{**md, "text": doc, "distance": None} for doc, md in zip(docs, metas)]

    # rag: semantic top-k within the cascade scope
    qvec = embed_query(query)
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
    hits = search(query, country=country, use_case=use_case, top_k=top_k, mode=mode)
    if not hits:
        return _EMPTY
    return _format(hits)
