"""
Chroma vector store access — single source of truth for the collection.

Local, in-process, persistent (no server). Cosine space, since the embeddings
are compared by direction. Both ingest.py (write) and retriever.py (read) go
through here so the collection is configured identically on both sides.
"""

from __future__ import annotations

import chromadb

from app.services.llm.rag import config

_client = None


def get_client() -> "chromadb.ClientAPI":
    global _client
    if _client is None:
        config.CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(config.CHROMA_PATH))
    return _client


def get_collection():
    """Get (or create) the normative collection, configured for cosine distance."""
    return get_client().get_or_create_collection(
        name=config.COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def reset_collection():
    """Drop and recreate the collection (used by ingestion --rebuild)."""
    client = get_client()
    try:
        client.delete_collection(config.COLLECTION_NAME)
    except Exception:
        pass
    return get_collection()


def reflag_quantitative(batch: int = 500) -> dict:
    """Recompute ``has_quantitative`` for every stored chunk, in place.

    The flag is a regex over the chunk text (chunker._QUANT_RE); when the regex
    changes there is no need to re-embed 1k chunks — metadata can be updated
    without touching the vectors. Returns per-country before/after counts.

        python -c "from app.services.llm.rag.store import reflag_quantitative as r; print(r())"
    """
    from app.services.llm.rag.chunker import _QUANT_RE

    col = get_collection()
    res = col.get(include=["documents", "metadatas"])
    ids, docs, metas = res["ids"], res["documents"], res["metadatas"]
    stats: dict = {}
    upd_ids, upd_meta = [], []
    for cid, doc, md in zip(ids, docs, metas):
        c = md.get("country", "?")
        s = stats.setdefault(c, {"chunks": 0, "before": 0, "after": 0})
        new = bool(_QUANT_RE.search(doc or ""))
        s["chunks"] += 1
        s["before"] += bool(md.get("has_quantitative"))
        s["after"] += new
        if bool(md.get("has_quantitative")) != new:
            upd_ids.append(cid)
            upd_meta.append({**md, "has_quantitative": new})
    for i in range(0, len(upd_ids), batch):
        col.update(ids=upd_ids[i:i + batch], metadatas=upd_meta[i:i + batch])
    stats["updated"] = len(upd_ids)
    return stats
