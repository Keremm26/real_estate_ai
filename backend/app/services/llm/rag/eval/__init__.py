"""
Retrieval evaluation for the normative RAG pipeline.

- ``gold_queries_<city>.json`` : hand-labelled items + relevant articles (ground truth), one file per city
- ``metrics``           : pure metric functions (recall@k, MRR, nDCG@k, ...)
- ``run``               : CLI that scores the retriever against the gold set
"""
