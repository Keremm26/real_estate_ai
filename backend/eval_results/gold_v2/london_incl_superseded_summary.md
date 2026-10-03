# Retrieval evaluation — normative RAG

**Scope:** UK / student_housing · embed `bge-m3` · 40 gold items / 40 runs · 1039 chunks in scope · superseded documents INCLUDED

## Overall retrieval quality (mean over runs)

| metric | @1 | @3 | @5 | @8 |
|---|---|---|---|---|
| recall | 0.588 | 0.750 | 0.850 | 0.912 |
| hit@k | 0.650 | 0.775 | 0.875 | 0.925 |
| nDCG | 0.650 | 0.711 | 0.753 | 0.775 |
| precision* | 0.650 | 0.300 | 0.200 | 0.134 |

**MRR = 0.744**

\* precision is capped at 1/k for single-relevant items — reported, not headline.

### by type

| type | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| conflict | 2 | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 |
| factual | 31 | 0.645 | 0.742 | 0.871 | 0.935 | 0.734 |
| lex_specialis | 1 | 0.500 | 0.500 | 0.500 | 0.500 | 1.000 |
| multi_article | 4 | 0.250 | 0.875 | 0.875 | 1.000 | 0.750 |
| temporal | 2 | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 |

### by tier

| tier | n | recall@1 | recall@3 | recall@5 | recall@8 | MRR |
|---|---|---|---|---|---|---|
| municipal | 12 | 0.625 | 0.792 | 0.958 | 0.958 | 0.829 |
| national | 20 | 0.600 | 0.650 | 0.750 | 0.850 | 0.665 |
| regional | 8 | 0.500 | 0.938 | 0.938 | 1.000 | 0.812 |

## Precedence (conflict / temporal items)

| runs | pair_recall | winner_first |
|---|---|---|
| 5 | 0.600 | 0.600 |

pair_recall = both rules surfaced within depth; winner_first = the prevailing rule was retrieved and ranks above the overridden one.

## Efficiency (context sent to the LLM)

| mode | ~tokens/query |
|---|---|
| dump (all 1039 in-scope chunks) | 485,654 |
| rag (top-8) | 5,140 |

**98.9% smaller context with RAG.**
