"""
Embedding client — talks to a local Ollama server (OpenAI-compatible infra
already used for the generation models).

Model-agnostic: the model name comes from ``config.EMBED_MODEL`` and the vector
dimension is whatever the model emits (never hardcoded). Swapping the embedder
is an env change + re-ingest, nothing here changes.
"""

from __future__ import annotations

from typing import List

import httpx

from app.services.llm.rag import config


class EmbeddingError(RuntimeError):
    """Raised when the embedding backend is unreachable or the model is missing."""


def _post_embed(inputs: List[str]) -> List[List[float]]:
    url = f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/embed"
    try:
        resp = httpx.post(
            url,
            json={"model": config.EMBED_MODEL, "input": inputs},
            timeout=config.EMBED_TIMEOUT_S,
        )
    except httpx.RequestError as e:
        raise EmbeddingError(
            f"Cannot reach Ollama at {config.OLLAMA_BASE_URL}. Is it running? ({e})"
        ) from e

    if resp.status_code == 404 or (
        resp.status_code >= 400 and "not found" in resp.text.lower()
    ):
        raise EmbeddingError(
            f"Embedding model '{config.EMBED_MODEL}' not available in Ollama. "
            f"Pull it first:  ollama pull {config.EMBED_MODEL}"
        )
    resp.raise_for_status()

    data = resp.json()
    embeddings = data.get("embeddings")
    if not embeddings:
        raise EmbeddingError(f"No embeddings returned by Ollama: {data}")
    return embeddings


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a list of texts, batched to keep request sizes sane."""
    if not texts:
        return []
    out: List[List[float]] = []
    batch = max(1, config.EMBED_BATCH_SIZE)
    for i in range(0, len(texts), batch):
        out.extend(_post_embed(texts[i : i + batch]))
    return out


def embed_query(text: str) -> List[float]:
    """Embed a single query string."""
    return _post_embed([text])[0]
