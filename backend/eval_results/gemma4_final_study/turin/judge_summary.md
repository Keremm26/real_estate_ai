# Blind pairwise judge — none vs rag (gemma-4)

Judge claude-sonnet-5-5 (effort medium, Batch API, rubric v1) · reference note shown · 50 pairs (27 identical → tie) · requests {'succeeded': 46} · cost ≈ $0.25

| criterion | rag wins | none wins | tie | missing | rag win rate (decided) | sign test p | position consistency |
|---|---|---|---|---|---|---|---|
| request_fit | 11 | 3 | 36 | 0 | 79% | 0.057 | 91% |
| legal_adequacy | 12 | 0 | 38 | 0 | 100% | 0.000 | 100% |
| ranking_quality | 15 | 5 | 30 | 0 | 75% | 0.041 | 87% |
| overall | 15 | 6 | 29 | 0 | 71% | 0.078 | 91% |

## Overall verdict by regulatory expectation

| activate | rag wins | none wins | tie | missing |
|---|---|---|---|---|
| True | 14 | 5 | 9 | 0 |
| False | 0 | 0 | 18 | 0 |
| either | 1 | 1 | 2 | 0 |

A verdict counts only when both presentation orders agree; disagreement is scored as a tie.
