"""
RAG normative pipeline.

Retrieval layer that feeds the regulatory agent: replaces the old
``load_regulatory_documents()`` "dump everything" strategy with a
router -> jurisdiction cascade -> semantic search over a local vector
store of article-level regulatory chunks.

Entry points are added as the modules land:
- ``ingest``    : offline — corpus xlsx shortlist -> fetch -> chunk -> embed -> Chroma
- ``retriever`` : online  — ``retrieve(query, country, use_case)``
"""
