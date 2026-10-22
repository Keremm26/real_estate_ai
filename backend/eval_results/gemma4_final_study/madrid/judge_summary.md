# Blind pairwise judge — none vs rag (gemma-4)

Judge claude-sonnet-5-5 (effort medium, Batch API, rubric v1) · reference note shown · 40 pairs (20 identical → tie) · requests {'succeeded': 40} · cost ≈ $0.22

| criterion | rag wins | none wins | tie | missing | rag win rate (decided) | sign test p | position consistency |
|---|---|---|---|---|---|---|---|
| request_fit | 9 | 5 | 26 | 0 | 64% | 0.424 | 85% |
| legal_adequacy | 9 | 1 | 30 | 0 | 90% | 0.021 | 95% |
| ranking_quality | 13 | 4 | 23 | 0 | 76% | 0.049 | 90% |
| overall | 12 | 5 | 23 | 0 | 71% | 0.143 | 85% |

## Overall verdict by regulatory expectation

| activate | rag wins | none wins | tie | missing |
|---|---|---|---|---|
| True | 9 | 4 | 12 | 0 |
| False | 0 | 0 | 5 | 0 |
| either | 3 | 1 | 6 | 0 |

A verdict counts only when both presentation orders agree; disagreement is scored as a tie.
