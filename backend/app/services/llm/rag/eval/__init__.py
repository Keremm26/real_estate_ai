"""
Retrieval evaluation for the normative RAG pipeline.

- ``gold_queries.json`` : hand-labelled queries + relevant articles (ground truth)
- ``metrics``           : pure metric functions (recall@k, MRR, nDCG@k, ...)
- ``run``               : CLI that scores the retriever against the gold set
"""
