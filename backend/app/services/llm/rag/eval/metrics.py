"""
Retrieval metrics — pure functions, no I/O, unit-testable.

All operate on ``flags``: a list of booleans in rank order, where ``flags[i]``
is True iff the item retrieved at rank i (0-based) is a relevant document.
``num_relevant`` is the total number of relevant documents that *exist* for the
query (the size of the gold set for that query), needed to normalise recall and
the ideal DCG.

Metric choice for this corpus (see gold_queries.json):
  - recall@k / MRR / nDCG@k are the headline metrics.
  - hit@k equals recall@k when a query has a single relevant doc, so it adds no
    information here — reported only for completeness.
  - precision@k is capped at 1/k for single-relevant queries, so it is reported
    with that caveat, not as a headline.
"""

from __future__ import annotations

from math import log2
from typing import List


def recall_at_k(flags: List[bool], num_relevant: int, k: int) -> float:
    """Fraction of the query's relevant docs that appear in the top k."""
    if num_relevant <= 0:
        return 0.0
    return sum(1 for f in flags[:k] if f) / num_relevant


def precision_at_k(flags: List[bool], k: int) -> float:
    """Fraction of the top k that are relevant. Capped at 1/k when only one is relevant."""
    if k <= 0:
        return 0.0
    return sum(1 for f in flags[:k] if f) / k


def hit_at_k(flags: List[bool], k: int) -> float:
    """1.0 if any relevant doc is in the top k, else 0.0 (a.k.a. success@k)."""
    return 1.0 if any(flags[:k]) else 0.0


def reciprocal_rank(flags: List[bool]) -> float:
    """1 / (rank of the first relevant doc); 0 if none retrieved."""
    for i, f in enumerate(flags):
        if f:
            return 1.0 / (i + 1)
    return 0.0


def dcg_at_k(flags: List[bool], k: int) -> float:
    """Discounted cumulative gain with binary relevance."""
    return sum((1.0 / log2(i + 2)) for i, f in enumerate(flags[:k]) if f)


def ndcg_at_k(flags: List[bool], num_relevant: int, k: int) -> float:
    """nDCG@k with binary relevance: DCG normalised by the ideal ranking's DCG."""
    if num_relevant <= 0:
        return 0.0
    ideal = [True] * min(num_relevant, k)
    idcg = dcg_at_k(ideal, k)
    if idcg == 0:
        return 0.0
    return dcg_at_k(flags, k) / idcg
