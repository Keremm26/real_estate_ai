# Blind pairwise judge — none vs rag (gemma-4)

Judge claude-sonnet-5-5 (effort medium, Batch API, rubric v1) · reference note shown · 40 pairs (22 identical → tie) · requests {'succeeded': 36} · cost ≈ $0.19

| criterion | rag wins | none wins | tie | missing | rag win rate (decided) | sign test p | position consistency |
|---|---|---|---|---|---|---|---|
| request_fit | 7 | 4 | 29 | 0 | 64% | 0.549 | 94% |
| legal_adequacy | 5 | 0 | 35 | 0 | 100% | 0.062 | 100% |
| ranking_quality | 7 | 4 | 29 | 0 | 64% | 0.549 | 78% |
| overall | 7 | 4 | 29 | 0 | 64% | 0.549 | 78% |

## Overall verdict by regulatory expectation

| activate | rag wins | none wins | tie | missing |
|---|---|---|---|---|
| True | 6 | 2 | 16 | 0 |
| False | 1 | 2 | 10 | 0 |
| either | 0 | 0 | 3 | 0 |

A verdict counts only when both presentation orders agree; disagreement is scored as a tie.
