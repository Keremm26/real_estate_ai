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
