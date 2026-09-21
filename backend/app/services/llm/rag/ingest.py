"""
Ingestion (offline) — build the normative vector store for a city slice.

    python -m app.services.llm.rag.ingest --city turin [--rebuild] [--force-fetch]

Pipeline per document: fetch (cached) -> article-level chunk -> metadata tag ->
embed -> upsert into Chroma. Run once per corpus change; retrieval is entirely
local afterwards.

Requires the embedding model to be available in Ollama
(``ollama pull <RAG_EMBED_MODEL>``). Everything before the embed step runs
without it, so ``--dry-run`` lets you inspect chunking with no model.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import List

from app.services.llm.rag import config, store
from app.services.llm.rag.chunker import chunk_document
from app.services.llm.rag.embedder import embed_texts
from app.services.llm.rag.fetch import fetch_text
from app.services.llm.rag.manifest import MANIFESTS, DocSpan, DocSpec
from app.services.llm.rag.metadata import ChunkMetadata


def _apply_span(text: str, span: DocSpan, doc_key: str) -> str:
    """Cut the cleaned text down to [start heading, end heading). Each regex
    must match exactly once — an index entry or a cross-reference matching too
    would silently ingest the wrong part, so ambiguity is an error."""
    bounds = []
    for edge in ("start", "end"):
        hits = list(re.finditer(span[edge], text, re.MULTILINE))
        if len(hits) != 1:
            raise ValueError(f"{doc_key}: span {edge!r} matched {len(hits)} times, expected 1")
        bounds.append(hits[0].start())
    if bounds[0] >= bounds[1]:
        raise ValueError(f"{doc_key}: span end precedes start")
    return text[bounds[0]:bounds[1]]


def _build_chunks(spec: DocSpec, *, force_fetch: bool):
    """Fetch + chunk one document, returning (ids, texts, metadatas)."""
    text = fetch_text(spec["doc_key"], spec["fetch_url"], force=force_fetch)
    if "span" in spec:
        text = _apply_span(text, spec["span"], spec["doc_key"])
    if config.CHUNK_STRATEGY == "llm":
        from app.services.llm.rag.llm_chunker import chunk_document_llm
        articles = chunk_document_llm(text, cache_key=spec["doc_key"])
    else:
        articles = chunk_document(text)
    ids, texts, metas = [], [], []
    for art in articles:
        md = ChunkMetadata(
            country=spec["country"],
            jurisdiction_level=spec["jurisdiction_level"],
            use_case=spec["use_case"],
            doc_type=spec["doc_type"],
            doc_name=spec["doc_name"],
            article_ref=art.article_ref,
            source_url=spec["url"],
            lang=spec["lang"],
            effective_date=spec["effective_date"],
            has_quantitative=art.has_quantitative,
        )
        ids.append(md.chunk_id())
        texts.append(art.text)
        metas.append(md.to_chroma())
    return ids, texts, metas


def ingest_city(city: str, *, rebuild: bool, force_fetch: bool, dry_run: bool,
                only_docs: List[str] | None = None) -> None:
    specs: List[DocSpec] = MANIFESTS.get(city)
    if not specs:
        raise SystemExit(f"Unknown city '{city}'. Known: {list(MANIFESTS)}")
    if only_docs:
        unknown = set(only_docs) - {s["doc_key"] for s in specs}
        if unknown:
            raise SystemExit(f"Unknown doc_key(s) for '{city}': {sorted(unknown)}")
        specs = [s for s in specs if s["doc_key"] in only_docs]

    print(f"Ingesting '{city}' — {len(specs)} document(s)")
    all_ids, all_texts, all_metas = [], [], []
    for spec in specs:
        try:
            ids, texts, metas = _build_chunks(spec, force_fetch=force_fetch)
        except Exception as e:  # noqa: BLE001 — one bad source shouldn't abort the rest
            print(f"  ! {spec['doc_key']}: fetch/chunk failed: {e}")
            continue
        quant = sum(1 for m in metas if m.get("has_quantitative"))
        print(f"  · {spec['doc_key']}: {len(ids)} articles ({quant} quantitative)")
        all_ids += ids
        all_texts += texts
        all_metas += metas

    print(f"Total: {len(all_ids)} chunks")
    if dry_run:
        print("[dry-run] skipping embed + store")
        return
    if not all_ids:
        raise SystemExit("Nothing to ingest (all fetches failed?).")

    col = store.reset_collection() if rebuild else store.get_collection()

    if not rebuild:
        # Re-ingesting a document REPLACES it: drop its previous chunks first so
        # refs that no longer exist (e.g. an uncapped 'Art. 8' superseded by
        # 'Art. 8 (part N)') don't survive as stale duplicates next to the new ones.
        for spec in specs:
            old = col.get(where={"$and": [{"country": spec["country"]}, {"doc_name": spec["doc_name"]}]})
            if old["ids"]:
                col.delete(ids=old["ids"])
                print(f"  - {spec['doc_key']}: removed {len(old['ids'])} previous chunk(s)")

    print(f"Embedding {len(all_texts)} chunks via '{config.EMBED_MODEL}' ...")
    vectors = embed_texts(all_texts)

    col.upsert(ids=all_ids, embeddings=vectors, documents=all_texts, metadatas=all_metas)
    print(f"Done. Collection '{config.COLLECTION_NAME}' now holds {col.count()} chunks.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Ingest a city slice into the normative store.")
    p.add_argument("--city", default="turin", help="manifest key (default: turin)")
    p.add_argument("--doc", action="append", default=None,
                   help="only (re-)ingest this doc_key; repeatable. Replaces the doc's existing chunks.")
    p.add_argument("--rebuild", action="store_true", help="drop the WHOLE collection first (all cities)")
    p.add_argument("--force-fetch", action="store_true", help="re-fetch sources, ignore cache")
    p.add_argument("--dry-run", action="store_true", help="fetch + chunk only, no embed/store")
    args = p.parse_args(argv)
    if args.rebuild and args.doc:
        p.error("--rebuild drops every city; it cannot be combined with --doc")
    ingest_city(args.city, rebuild=args.rebuild, force_fetch=args.force_fetch, dry_run=args.dry_run,
                only_docs=args.doc)


if __name__ == "__main__":
    sys.exit(main())
