# Retrieval evaluation — normative RAG

**Scope:** ES / student_housing · embed `bge-m3` · 40 gold items / 80 runs · 3164 chunks in scope

## Overall retrieval quality (mean over runs)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.400 | 0.662 | 0.762 | 0.829 |
| hit@k | 0.487 | 0.713 | 0.775 | 0.838 |
| nDCG | 0.487 | 0.581 | 0.626 | 0.651 |
| precision* | 0.487 | 0.271 | 0.190 | 0.130 |

**MRR = 0.614**

\* precision is capped at 1/k for single-relevant items — reported, not headline.

### by lang

| lang | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| en | 40 | 0.388 | 0.650 | 0.725 | 0.775 | 0.584 |
| es | 40 | 0.412 | 0.675 | 0.800 | 0.883 | 0.643 |

### by type

| type | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| conflict | 4 | 0.500 | 0.875 | 0.875 | 1.000 | 1.000 |
| factual | 64 | 0.406 | 0.672 | 0.750 | 0.812 | 0.562 |
| lex_specialis | 2 | 0.500 | 0.750 | 1.000 | 1.000 | 1.000 |
| multi_article | 10 | 0.300 | 0.500 | 0.750 | 0.833 | 0.717 |

### by tier

| tier | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| municipal | 38 | 0.500 | 0.763 | 0.895 | 0.947 | 0.791 |
| national | 24 | 0.292 | 0.542 | 0.667 | 0.764 | 0.455 |
| regional | 18 | 0.333 | 0.611 | 0.611 | 0.667 | 0.451 |

## Precedence (conflict / temporal items)

| runs | pair_recall | winner_first |
|---|---|---|
| 4 | 1.000 | 1.000 |

pair_recall = both rules surfaced within depth; winner_first = the prevailing rule was retrieved and ranks above the overridden one.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 3164 in-scope chunks) | 1,129,433 |
| rag (top-8) | 5,434 |

**99.5% smaller context with RAG.**
